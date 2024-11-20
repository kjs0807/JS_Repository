pub mod mid;

use pcap::Capture;
use std::error::Error;
use std::fs::File;
use std::io::{BufRead, BufReader, Write};
use std::collections::HashMap;
use serde_json::json;

/* 
 Jay: 
  - if you put the path of the file in the body of the function, it is hard to reuse it
  - I think your function do more than its name indicates. variables and functinos should be named more descriptively
  - I may implment parse or from_file to MidPrice struct
  # Example
  pub struct MidPrice {
      pub price: Vec<f64>,
  }

  impl From<File> for MidPrice {
      fn from(file: File) -> Self {
          let reader = BufReader::new(file);
          let time_price_map: HashMap<String, f64> = serde_json::from_reader(reader)?;
          let mut mid_price = MidPrice { price: Vec::new() };
          for (_timestamp, price) in time_price_map {
              mid_price.price.push(price);
          }
          Ok(mid_price)
      }
  }

  or 

  impl MidPrice {
    pub fn from_txtfile(file_path: &str) -> Result<Self, Box<dyn Error>> {
        let file = File::open(file_path)?;
        let reader = BufReader::new(file);
        let time_price_map: HashMap<String, f64> = serde_json::from_reader(reader)?;
        let mut mid_price = MidPrice { price: Vec::new() };
        for (_timestamp, price) in time_price_map {
            mid_price.price.push(price);
        }
        Ok(mid_price)
    }
  }
*/
fn calculate_ten_y_midprice() -> Result<(), Box<dyn Error>> {
    // this may be a input for the function not hardcoded? 
    // format! makes a new String object but File::open takes &str so the format! is not necessary
    // let input_path: &str = "D:\\HFT\\Koscom Data\\pcap_B606F_10yktbf.txt"; <- then this is made in compile time
    let input_path = format!("D:\\HFT\\Koscom Data\\pcap_B606F_10yktbf.txt");
    println!("Starting mid-price calculation...");
    println!("Reading from: {}", input_path);
    
    let input_file = match File::open(&input_path) {
        Ok(file) => file,
        Err(e) => {
            println!("Error opening input file: {}", e);
            return Err(e.into());
        }
    };
    
    let reader = BufReader::new(input_file);
    let mut time_price_map: HashMap<String, f64> = HashMap::new();
    let mut lines_processed = 0;

    for line in reader.lines() {
        let line = line?;
        lines_processed += 1;
        
        if let Some(data) = line.split(": ").nth(1) {
            if data.len() >= 65 {
                let timestamp = &data[35..47];
                
                if timestamp > "090000000000" {
                    // Jay: variable names should be more descriptive, e.g., best_bid_price_str, best_ask_price_str
                    let price1_str = &data[47..56];
                    let price2_str = &data[56..65];
                    
                    if let (Ok(price1), Ok(price2)) = (
                        price1_str.trim().parse::<f64>(), // Jay: just a note, for featuring this is Ok, but for I/O, you have to be very careful in parsing f64
                        price2_str.trim().parse::<f64>()
                    ) {
                        let mid_price = (price1 + price2) / 2.0;
                        time_price_map.insert(timestamp.to_string(), mid_price);
                        /* Jay: some remarks on to_string
                         - to_string() makes a new String object, this is quite heavy operation if it is called frequently
                         - what's happening in makeing String: 
                            - allocate memory for the new string
                            - copy the content of the original string
                            - return the new string
                         - memory of empty string is 24 bytes: 8 bytes for pointer, 8 bytes for capacity, 8 bytes for length
                         - your content is 12 bytes, but it will highly likely behaves like 16 bytes in 64bit system beacuse of padding
                         - so, the key of the hashmap is 36 or 40 bytes, if you use Unix Nano, it will be 8 bytes (u64)
                         - memory is very crucial in performance. Most runtime performance is determined by communication not by computation
                         - CPU moves data from memory to cache by chunks, if your datastructure is too big, it will be moved to cache too frequently
                        */
                    }
                }
            }
        }
        
        if lines_processed % 1000 == 0 {
            println!("Processed {} lines...", lines_processed);
        }
    }

    // HashMap을 Vec으로 변환하고 timestamp 기준으로 정렬
    let mut sorted_entries: Vec<_> = time_price_map.into_iter().collect();
    /* Jay:
    1) If your purpose is to sort the mid price, you may want to use BtreeMap instead of HashMap. BtreeMap is inherently sorted by key.
    2) A suggestion: Let me assume the txt file is filed with the same order of the pcap file, i.e., receival order
       In that case, there may be some useful observations:
        - mostly, the data is ordered
        - but still very rarelly, the data is out of order (the out of order data is mostly due to the network congestion)
        - In live trading, we may not need the out of data
       Thus, if you just dropping the out of order data, you can save a lot of memory and time without much loss of information
       # Example pseudo code
       let mut mid_prices = Vec<f64>::new();
       let last_timestamp = "000000000000";
       for data in data_in_lines {
            let timestamp = &data[35..47];
            let mid_price = ...;
            if timestamp < last_timestamp {
                continue;
            } else {
                last_timestamp = timestamp;
                mid_prices.push(mid_price);
            }
       }
     */
    sorted_entries.sort_by(|a, b| a.0.cmp(&b.0));  // timestamp 기준 오름차순 정렬

    // 정렬된 데이터로 새로운 JSON 객체 생성
    let sorted_json = json!(sorted_entries.into_iter().collect::<HashMap<_, _>>());

    let json_path = format!("D:\\HFT\\Koscom Data\\time_price.json");
    println!("Creating JSON file: {}", json_path);
    
    let json_file = File::create(&json_path)?;
    serde_json::to_writer_pretty(json_file, &sorted_json)?;

    // Jay: In this tutorial, it is totally fine, but do note that print in console is very slow so it is not recommended in production code
    println!("Mid-price calculation completed!");
    println!("Total lines processed: {}", lines_processed);
    println!("Total entries in JSON: {}", sorted_json.as_object().unwrap().len());
    println!("JSON file saved to: {}", json_path);

    Ok(())
}

// Jay: the flow is like iterating Pcap -> making txt -> reiterating txt -> making MidPrice
fn main() -> Result<(), Box<dyn Error>> {
    println!("Starting PCAP processing...");
    let mut cap = Capture::from_file("D:\\HFT\\Koscom Data\\koscom_udp_2024-09-27.pcap")?;
    
    let mut files = HashMap::new();
    let file_configs = [
        ("3yktbf", "KR4165"),
        ("10yktbf", "KR4167"),
        ("30yktbf", "KR4170"),
        ("default", ""),
    ];

    println!("Creating output files...");
    for (suffix, _) in &file_configs {
        let path = format!("D:\\HFT\\Koscom Data\\pcap_B606F_{}.txt", suffix);
        println!("Creating file: {}", path);
        let file = File::create(&path)?;
        files.insert(suffix.to_string(), (file, 0));
    }

    let mut packet_count = 0;
    println!("Processing PCAP file...");

    while let Ok(packet) = cap.next() {
        packet_count += 1;

        // too much unneccessary allocation
        // you pass the iteration unless it does not start with "B606F"
        // but collection with Vec<u8> allocates and copy which is very heavy operation
        // why not just like
        // let clipped_data = packet.data[42..];
        // if clipped_data.starts_with(b"B606F") {...
        let printable: Vec<u8> = packet.data[42..]
            .iter()
            .filter(|&&byte| byte >= 32 && byte <= 126)
            // this compresses the data (shrink the payload if there is not ascii thing), also bond A0 data contains Korean characters, so this part does not act as you expected
            .copied()
            .collect();

        if !printable.is_empty() {
            // why not just work with byte string directly? for example printable.starts_with(b"B606F")
            if let Ok(str_data) = std::str::from_utf8(&printable) {
                if str_data.contains("B606F") {
                    if str_data.len() >= 23 {
                        let target = &str_data[17..23];
                        
                        let file_suffix = match target {
                            "KR4165" => "3yktbf",
                            "KR4167" => "10yktbf",
                            "KR4170" => "30yktbf",
                            _ => "default",
                        };

                        if let Some((file, sequence_number)) = files.get_mut(file_suffix) {
                            writeln!(file, "{}: {}", sequence_number, str_data)?;
                            if *sequence_number % 1000 == 0 {
                                println!("Processed {} packets for {} file", sequence_number, file_suffix);
                            }
                            *sequence_number += 1;
                        }
                    }
                }
            }
        }
    }

    // 명시적으로 파일들을 닫음
    println!("\nClosing all files...");
    drop(files);
    println!("All files closed successfully.");

    println!("\nPCAP processing completed:");
    println!("Total packets processed: {}", packet_count);
    
    println!("\n=== Starting mid-price calculation phase ===");
    match calculate_ten_y_midprice() {
        Ok(_) => println!("Mid-price calculation completed successfully!"),
        Err(e) => println!("Error during mid-price calculation: {}", e),
    }

    Ok(())
}