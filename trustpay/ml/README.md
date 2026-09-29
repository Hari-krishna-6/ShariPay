# TrustPay ML Risk Engine

This is a prototype Random Forest risk model trained only on synthetic transactions. Its fraud probability is an input to a future backend policy layer; the model does not submit chaincode transactions or modify DRUNIX state.

## Environment and dependencies

Python 3.11 or newer is recommended. The current local verification environment is Python 3.14.6. Dependencies are listed in `requirements.txt`.

```bash
cd trustpay/ml
python -m venv .venv
# Linux/WSL: source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## Reproducible workflow

Generate at least 20,000 synthetic rows (up to 100,000 is supported):

```bash
python -m src.generate_data --rows 20000 --seed 42 --output data/transactions.csv
```

Train and evaluate the Random Forest; actual test metrics and class counts print to stdout:

```bash
python -m src.train --dataset data/transactions.csv --model models/fraud_model.joblib
```

Run the tests:

```bash
python -m pytest -q
```

Run inference with a JSON object containing the 15 named model features:

```bash
python -m src.predict --model models/fraud_model.joblib --input transaction.json
```

The trained artifact is `models/fraud_model.joblib`; the generated CSV and artifact are git-ignored and can be regenerated with the commands above.

## Dataset assumptions

All records and labels are fictional. Scenario mixtures create correlated patterns for account takeover, velocity attacks, unusually large amounts, new-beneficiary fraud, device/location anomalies, and failed-attempts-followed-by-large-payments. Fraud labels are sampled probabilistically from several combined signals, so neither a single feature nor scenario membership perfectly determines `is_fraud`. The dataset is for software demonstration, not an estimate of real-world fraud prevalence.

Training uses only the 15 columns in `src.features.FEATURE_NAMES`. User/transaction/beneficiary/device identifiers and `is_fraud` are not model inputs; the feature validator rejects those fields if they are passed directly.

## Model and metrics

The model is a `RandomForestClassifier` with `class_weight="balanced_subsample"`, stratified train/test split, fixed random state 42, and 300 trees. Training calculates accuracy, precision, recall, F1, ROC-AUC, confusion matrix, and false-positive rate from the held-out split. Accuracy alone is insufficient because a class-imbalanced dataset can have high accuracy while missing many fraud cases.

Risk thresholds are centralized in `src/config.py`: score `<0.30` LOW, `0.30–<0.70` MEDIUM, `>=0.70` HIGH. These are prototype policy thresholds, not universal financial-fraud thresholds.

`risk_factors` are separate human-readable rule flags derived from input features. They are not feature attributions or explanations produced by the Random Forest.

## Future integration

The intended path is payment request → backend feature extraction → `predict_risk()` → policy decision → official DRUNIX Gateway client → TrustPay chaincode. This module has no direct database, FastAPI, Gateway, or blockchain dependency. It returns a probability and factors only; a later backend/policy layer decides the chaincode function to call.