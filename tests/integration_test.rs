use std::error::Error;
use std::fs::File;
use std::io::{BufRead, BufReader};
use std::collections::HashMap;

// 데이터 로딩 함수들
fn load_json_data(date: &str) -> Result<HashMap<String, f64>, Box<dyn Error>> {
    let json_path = format!("D:\\HFT\\Koscom Data\\time_price_{}.json", date);
    let json_file = File::open(&json_path)?;
    let json_data: HashMap<String, f64> = serde_json::from_reader(json_file)?;
    Ok(json_data)
}

fn load_original_data(date: &str) -> Result<HashMap<String, f64>, Box<dyn Error>> {
    let txt_path = format!("D:\\HFT\\Koscom Data\\pcap_B606F_10yktbf_{}.txt", date);
    let txt_file = File::open(&txt_path)?;
    let reader = BufReader::new(txt_file);
    
    let mut original_data: HashMap<String, f64> = HashMap::new();
    
    for line in reader.lines() {
        let line = line?;
        if let Some(data) = line.split(": ").nth(1) {
            if data.len() >= 65 {
                let timestamp = data[35..47].to_string();
                if timestamp.as_str() > "090000000000" {  // timestamp.as_str() 사용
                    let price1_str = &data[47..56];
                    let price2_str = &data[56..65];
                    if let (Ok(price1), Ok(price2)) = (
                        price1_str.trim().parse::<f64>(),
                        price2_str.trim().parse::<f64>()
                    ) {
                        let mid_price = (price1 + price2) / 2.0;
                        original_data.insert(timestamp, mid_price);
                    }
                }
            }
        }
    }
    Ok(original_data)
}

// 메인 함수
fn main() -> Result<(), Box<dyn Error>> {
    let date = chrono::Local::now().format("%Y%m%d").to_string();
    
    let json_data = load_json_data(&date)?;
    let original_data = load_original_data(&date)?;
    
    println!("Data loaded successfully");
    println!("JSON entries: {}", json_data.len());
    println!("Original entries: {}", original_data.len());
    
    Ok(())
}

// 테스트 모듈
#[cfg(test)]
mod tests {
    use super::*;
    
    const EPSILON: f64 = 0.000001;  // 부동소수점 비교를 위한 오차 허용값

    #[test]
    fn test_data_completeness() -> Result<(), Box<dyn Error>> {
        let date = chrono::Local::now().format("%Y%m%d").to_string();
        let json_data = load_json_data(&date)?;
        let original_data = load_original_data(&date)?;
        
        // 데이터 개수가 동일한지 확인
        assert_eq!(
            json_data.len(), 
            original_data.len(),
            "JSON and original data have different number of entries"
        );
        Ok(())
    }

    #[test]
    fn test_timestamp_existence() -> Result<(), Box<dyn Error>> {
        let date = chrono::Local::now().format("%Y%m%d").to_string();
        let json_data = load_json_data(&date)?;
        let original_data = load_original_data(&date)?;
        
        // 모든 timestamp가 양쪽 데이터에 존재하는지 확인
        for timestamp in original_data.keys() {
            assert!(
                json_data.contains_key(timestamp),
                "Timestamp {} exists in original but missing in JSON",
                timestamp
            );
        }

        for timestamp in json_data.keys() {
            assert!(
                original_data.contains_key(timestamp),
                "Timestamp {} exists in JSON but missing in original",
                timestamp
            );
        }
        Ok(())
    }

    #[test]
    fn test_price_values() -> Result<(), Box<dyn Error>> {
        let date = chrono::Local::now().format("%Y%m%d").to_string();
        let json_data = load_json_data(&date)?;
        let original_data = load_original_data(&date)?;
        
        // 각 timestamp에 대한 가격 값이 동일한지 확인
        for (timestamp, orig_price) in &original_data {
            if let Some(json_price) = json_data.get(timestamp) {
                assert!(
                    (orig_price - json_price).abs() < EPSILON,
                    "Price mismatch for timestamp {}: Original={}, JSON={}",
                    timestamp, orig_price, json_price
                );
            }
        }
        Ok(())
    }
}