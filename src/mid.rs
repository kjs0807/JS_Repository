use serde::{Deserialize, Serialize};

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
    fn test_mid_push() {
        let mut mid = Mid { mid_price: Vec::new() };

        mid.push(100.0);
        mid.push(200.0);
        mid.push(300.0);

        let mid_str = serde_json::to_string(&mid).unwrap();
        println!("{:?}", mid.clone());
        dbg!(mid.clone());
        // save fie
        std::fs::write("mid.json", mid_str.clone()).unwrap();
        println!("{}", mid_str);
        let recoverd_mid: Mid = serde_json::from_str(&mid_str).unwrap();

        assert_eq!(mid, recoverd_mid);
    }
}