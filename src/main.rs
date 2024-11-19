pub mod mid;

use pcap::Capture;
use std::error::Error;
use std::fs::File;
use std::io::{BufRead, BufReader, Write};
use std::collections::HashMap;
use serde_json::json;


fn calculate_ten_y_midprice() -> Result<(), Box<dyn Error>> {
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
                    let price1_str = &data[47..56];
                    let price2_str = &data[56..65];
                    
                    if let (Ok(price1), Ok(price2)) = (
                        price1_str.trim().parse::<f64>(),
                        price2_str.trim().parse::<f64>()
                    ) {
                        let mid_price = (price1 + price2) / 2.0;
                        time_price_map.insert(timestamp.to_string(), mid_price);
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
    sorted_entries.sort_by(|a, b| a.0.cmp(&b.0));  // timestamp 기준 오름차순 정렬

    // 정렬된 데이터로 새로운 JSON 객체 생성
    let sorted_json = json!(sorted_entries.into_iter().collect::<HashMap<_, _>>());

    let json_path = format!("D:\\HFT\\Koscom Data\\time_price.json");
    println!("Creating JSON file: {}", json_path);
    
    let json_file = File::create(&json_path)?;
    serde_json::to_writer_pretty(json_file, &sorted_json)?;

    println!("Mid-price calculation completed!");
    println!("Total lines processed: {}", lines_processed);
    println!("Total entries in JSON: {}", sorted_json.as_object().unwrap().len());
    println!("JSON file saved to: {}", json_path);

    Ok(())
}

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

        let printable: Vec<u8> = packet.data[42..]
            .iter()
            .filter(|&&byte| byte >= 32 && byte <= 126)
            .copied()
            .collect();

        if !printable.is_empty() {
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