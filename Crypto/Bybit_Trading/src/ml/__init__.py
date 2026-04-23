"""ML pattern strategy subsystem.

Components:
- types: shared dataclasses
- helpers: building blocks (MTF align, indicators, divergence, candle)
- patterns: BasePattern + concrete patterns
- event_dataset_builder: pattern → ML dataset
- trainer: XGBoost + Optuna HPO
- validator: WalkForward retrain + Overfit detection
- persistence: model/meta save/load
- report: Failure Report generator
"""
