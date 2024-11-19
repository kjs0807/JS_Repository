use serde::{Deserialize, Serialize};
use std::io::BufReader;
use std::fs::File;
use std::collections::HashMap;
use std::error::Error;


#[derive(Debug, Serialize, Deserialize, PartialEq, Clone)]
pub struct Mid {
    pub mid_price: Vec<f64>,
}

impl Mid {
    pub fn push(&mut self, mid_price: f64) {
        self.mid_price.push(mid_price);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_mid_push() -> Result<(), Box<dyn Error>> {
        let mut mid = Mid { mid_price: Vec::new() };
        
        // JSON 파일 읽기
        let file_path = "D:\\HFT\\Koscom Data\\time_price.json";
        let file = File::open(file_path)?;
        let reader = BufReader::new(file);
        
        // JSON을 HashMap으로 파싱
        let time_price_map: HashMap<String, f64> = serde_json::from_reader(reader)?;
        
        // HashMap의 값들을 mid_price 벡터에 추가
        for (_timestamp, price) in time_price_map {
            mid.push(price);
        }

        // 직렬화하여 JSON 문자열로 변환
        let mid_str = serde_json::to_string(&mid)?;
        
        // 디버깅을 위한 출력
        println!("Total prices loaded: {}", mid.mid_price.len());
        println!("First few prices: {:?}", &mid.mid_price.iter().take(5).collect::<Vec<_>>());
        
        // JSON 파일로 저장
        std::fs::write("mid.json", mid_str.clone())?;
        
        // JSON 문자열에서 다시 Mid 구조체로 역직렬화
        let recovered_mid: Mid = serde_json::from_str(&mid_str)?;

        // 원본과 역직렬화된 객체가 같은지 검증
        assert_eq!(mid, recovered_mid);
        
        Ok(())
    }
}