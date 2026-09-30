package main

import (
	"encoding/json"
	"fmt"
	"math"
	"regexp"
	"sort"
	"strings"
	"time"

	"github.com/hyperledger/fabric-contract-api-go/v2/contractapi"
)

const paymentKeyPrefix = "payment:"
const paymentHistoryKeyPrefix = "payment-history:"
const paymentHistoryCountKeyPrefix = "payment-history-count:"
const accountKeyPrefix = "account:"

type PaymentStatus string

const (
	StatusCreated              PaymentStatus = "CREATED"
	StatusRiskAssessed         PaymentStatus = "RISK_ASSESSED"
	StatusApproved             PaymentStatus = "APPROVED"
	StatusHeld                 PaymentStatus = "HELD"
	StatusVerificationRequired PaymentStatus = "VERIFICATION_REQUIRED"
	StatusCompleted            PaymentStatus = "COMPLETED"
	StatusRejected             PaymentStatus = "REJECTED"
)

type PaymentTransaction struct {
	TransactionID        string        `json:"transactionId"`
	SenderID             string        `json:"senderId"`
	ReceiverID           string        `json:"receiverId"`
	SenderAccountID      string        `json:"senderAccountId,omitempty"`
	ReceiverAccountID    string        `json:"receiverAccountId,omitempty"`
	Amount               int64         `json:"amount"`
	AmountDecimal        string        `json:"amountDecimal,omitempty"`
	AmountMinor          int64         `json:"amountMinor,omitempty"`
	Currency             string        `json:"currency"`
	RiskScore            float64       `json:"riskScore"`
	RiskLevel            string        `json:"riskLevel"`
	PolicyDecision       string        `json:"policyDecision"`
	Status               PaymentStatus `json:"status"`
	CreatedAt            string        `json:"createdAt"`
	UpdatedAt            string        `json:"updatedAt"`
	VerificationRequired bool          `json:"verificationRequired"`
}

type PaymentAccount struct {
	AccountID    string `json:"accountId"`
	OwnerID      string `json:"ownerId"`
	BalanceMinor int64  `json:"balanceMinor"`
	Currency     string `json:"currency"`
}

type PaymentHistoryEntry struct {
	TransactionID string             `json:"transactionId"`
	Sequence      uint64             `json:"sequence"`
	Timestamp     string             `json:"timestamp"`
	Status        PaymentStatus      `json:"status"`
	Payment       PaymentTransaction `json:"payment"`
}

type PaymentContract struct {
	contractapi.Contract
}

var currencyPattern = regexp.MustCompile(`^[A-Z]{3}$`)

func (c *PaymentContract) CreatePayment(ctx contractapi.TransactionContextInterface, transactionID, senderID, receiverID string, amount int64, currency string) (*PaymentTransaction, error) {
	if err := requireAuthorizedOperator(ctx); err != nil {
		return nil, err
	}
	transactionID = strings.TrimSpace(transactionID)
	senderID = strings.TrimSpace(senderID)
	receiverID = strings.TrimSpace(receiverID)
	currency = strings.TrimSpace(currency)
	if transactionID == "" {
		return nil, fmt.Errorf("transaction ID cannot be empty")
	}
	if senderID == "" {
		return nil, fmt.Errorf("sender ID cannot be empty")
	}
	if receiverID == "" {
		return nil, fmt.Errorf("receiver ID cannot be empty")
	}
	if senderID == receiverID {
		return nil, fmt.Errorf("sender and receiver must differ")
	}
	if amount <= 0 {
		return nil, fmt.Errorf("amount must be greater than zero whole INR units")
	}
	if !currencyPattern.MatchString(currency) {
		return nil, fmt.Errorf("currency must be a three-letter uppercase code")
	}

	key := paymentKey(transactionID)
	existing, err := ctx.GetStub().GetState(key)
	if err != nil {
		return nil, fmt.Errorf("read payment key: %w", err)
	}
	if existing != nil {
		return nil, fmt.Errorf("payment %q already exists", transactionID)
	}

	if amount > math.MaxInt64/100 {
		return nil, fmt.Errorf("amount is too large")
	}
	return createPaymentState(ctx, transactionID, senderID, receiverID, "", "", amount, amount*100, currency)
}

func (c *PaymentContract) SubmitPayment(
	ctx contractapi.TransactionContextInterface,
	transactionID, senderID, receiverID, senderAccountID, receiverAccountID string,
	amountMinor, senderInitialBalanceMinor, receiverInitialBalanceMinor int64,
	currency string,
	riskScore float64,
	riskLevel, policyDecision string,
) (*PaymentTransaction, error) {
	if err := requireAuthorizedOperator(ctx); err != nil {
		return nil, err
	}
	transactionID = strings.TrimSpace(transactionID)
	senderID = strings.TrimSpace(senderID)
	receiverID = strings.TrimSpace(receiverID)
	senderAccountID = strings.TrimSpace(senderAccountID)
	receiverAccountID = strings.TrimSpace(receiverAccountID)
	currency = strings.TrimSpace(currency)
	riskLevel = strings.ToUpper(strings.TrimSpace(riskLevel))
	policyDecision = strings.ToUpper(strings.TrimSpace(policyDecision))
	if transactionID == "" || senderID == "" || receiverID == "" || senderAccountID == "" || receiverAccountID == "" {
		return nil, fmt.Errorf("transaction and account identities cannot be empty")
	}
	if senderID == receiverID || senderAccountID == receiverAccountID {
		return nil, fmt.Errorf("sender and receiver must differ")
	}
	if amountMinor <= 0 || senderInitialBalanceMinor < 0 || receiverInitialBalanceMinor < 0 {
		return nil, fmt.Errorf("amount must be positive and initial balances cannot be negative")
	}
	if !currencyPattern.MatchString(currency) {
		return nil, fmt.Errorf("currency must be a three-letter uppercase code")
	}
	riskLevel, policyDecision, err := validateRiskAssessment(riskScore, riskLevel, policyDecision)
	if err != nil {
		return nil, err
	}
	if existingBytes, err := ctx.GetStub().GetState(paymentKey(transactionID)); err != nil {
		return nil, fmt.Errorf("read payment key: %w", err)
	} else if existingBytes != nil {
		var existing PaymentTransaction
		if err := json.Unmarshal(existingBytes, &existing); err != nil {
			return nil, fmt.Errorf("decode existing payment: %w", err)
		}
		if existing.SenderID == senderID && existing.ReceiverID == receiverID &&
			existing.SenderAccountID == senderAccountID && existing.ReceiverAccountID == receiverAccountID &&
			existing.AmountMinor == amountMinor && existing.Currency == currency &&
			existing.RiskScore == riskScore && existing.RiskLevel == riskLevel && existing.PolicyDecision == policyDecision {
			return &existing, nil
		}
		return nil, fmt.Errorf("payment %q already exists with different payment data", transactionID)
	}
	sender, err := ensureAccount(ctx, senderAccountID, senderID, senderInitialBalanceMinor, currency)
	if err != nil {
		return nil, err
	}
	receiver, err := ensureAccount(ctx, receiverAccountID, receiverID, receiverInitialBalanceMinor, currency)
	if err != nil {
		return nil, err
	}
	if policyDecision == "APPROVE" {
		if sender.BalanceMinor < amountMinor {
			return nil, fmt.Errorf("insufficient ledger balance")
		}
		if receiver.BalanceMinor > math.MaxInt64-amountMinor {
			return nil, fmt.Errorf("receiver ledger balance would overflow")
		}
	}
	payment, err := createPaymentState(
		ctx, transactionID, senderID, receiverID, senderAccountID, receiverAccountID,
		amountMinor/100, amountMinor, currency,
	)
	if err != nil {
		return nil, err
	}
	payment.RiskScore = riskScore
	payment.RiskLevel = riskLevel
	payment.PolicyDecision = policyDecision
	if err := setPaymentStatusWithSequence(ctx, payment, StatusRiskAssessed, 2); err != nil {
		return nil, err
	}
	switch policyDecision {
	case "APPROVE":
		if err := setPaymentStatusWithSequence(ctx, payment, StatusApproved, 3); err != nil {
			return nil, err
		}
		sender.BalanceMinor -= amountMinor
		receiver.BalanceMinor += amountMinor
		if err := saveAccount(ctx, sender); err != nil {
			return nil, err
		}
		if err := saveAccount(ctx, receiver); err != nil {
			return nil, err
		}
		if err := setPaymentStatusWithSequence(ctx, payment, StatusCompleted, 4); err != nil {
			return nil, err
		}
		return payment, nil
	case "VERIFY":
		payment.VerificationRequired = true
		if err := setPaymentStatusWithSequence(ctx, payment, StatusVerificationRequired, 3); err != nil {
			return nil, err
		}
	case "HOLD":
		if err := setPaymentStatusWithSequence(ctx, payment, StatusHeld, 3); err != nil {
			return nil, err
		}
	case "REJECT":
		if err := setPaymentStatusWithSequence(ctx, payment, StatusRejected, 3); err != nil {
			return nil, err
		}
	}
	return payment, nil
}

func (c *PaymentContract) AssessRisk(ctx contractapi.TransactionContextInterface, transactionID string, riskScore float64, riskLevel, policyDecision string) (*PaymentTransaction, error) {
	if err := requireAuthorizedOperator(ctx); err != nil {
		return nil, err
	}
	var err error
	riskLevel, policyDecision, err = validateRiskAssessment(riskScore, riskLevel, policyDecision)
	if err != nil {
		return nil, err
	}
	payment, err := loadPayment(ctx, transactionID)
	if err != nil {
		return nil, err
	}
	if payment.Status != StatusCreated {
		return nil, invalidTransition(payment.Status, StatusRiskAssessed)
	}
	payment.RiskScore = riskScore
	payment.RiskLevel = riskLevel
	payment.PolicyDecision = policyDecision
	if err := setStatus(ctx, payment, StatusRiskAssessed); err != nil {
		return nil, err
	}
	return payment, nil
}

func (c *PaymentContract) ApprovePayment(ctx contractapi.TransactionContextInterface, transactionID string) (*PaymentTransaction, error) {
	if err := requireAuthorizedOperator(ctx); err != nil {
		return nil, err
	}
	payment, err := loadPayment(ctx, transactionID)
	if err != nil {
		return nil, err
	}
	if payment.Status == StatusRiskAssessed && payment.PolicyDecision != "APPROVE" {
		return nil, fmt.Errorf("policy decision %q does not permit direct approval", payment.PolicyDecision)
	}
	if payment.Status == StatusVerificationRequired {
		payment.PolicyDecision = "APPROVE"
		payment.VerificationRequired = false
	} else if payment.Status != StatusRiskAssessed {
		return nil, invalidTransition(payment.Status, StatusApproved)
	}
	if err := setStatus(ctx, payment, StatusApproved); err != nil {
		return nil, err
	}
	return payment, nil
}

func (c *PaymentContract) HoldPayment(ctx contractapi.TransactionContextInterface, transactionID string) (*PaymentTransaction, error) {
	if err := requireAuthorizedOperator(ctx); err != nil {
		return nil, err
	}
	payment, err := loadPayment(ctx, transactionID)
	if err != nil {
		return nil, err
	}
	if payment.Status != StatusRiskAssessed {
		return nil, invalidTransition(payment.Status, StatusHeld)
	}
	if payment.PolicyDecision != "HOLD" {
		return nil, fmt.Errorf("policy decision %q does not permit hold", payment.PolicyDecision)
	}
	if err := setStatus(ctx, payment, StatusHeld); err != nil {
		return nil, err
	}
	return payment, nil
}

func (c *PaymentContract) RequestVerification(ctx contractapi.TransactionContextInterface, transactionID string) (*PaymentTransaction, error) {
	if err := requireAuthorizedOperator(ctx); err != nil {
		return nil, err
	}
	payment, err := loadPayment(ctx, transactionID)
	if err != nil {
		return nil, err
	}
	allowed := (payment.Status == StatusRiskAssessed && payment.PolicyDecision == "VERIFY") || payment.Status == StatusHeld
	if !allowed {
		return nil, invalidTransition(payment.Status, StatusVerificationRequired)
	}
	payment.PolicyDecision = "VERIFY"
	payment.VerificationRequired = true
	if err := setStatus(ctx, payment, StatusVerificationRequired); err != nil {
		return nil, err
	}
	return payment, nil
}

func (c *PaymentContract) CompletePayment(ctx contractapi.TransactionContextInterface, transactionID string) (*PaymentTransaction, error) {
	if err := requireAuthorizedOperator(ctx); err != nil {
		return nil, err
	}
	payment, err := loadPayment(ctx, transactionID)
	if err != nil {
		return nil, err
	}
	if payment.Status != StatusApproved {
		return nil, invalidTransition(payment.Status, StatusCompleted)
	}
	if payment.SenderAccountID == "" || payment.ReceiverAccountID == "" || payment.AmountMinor <= 0 {
		if err := setStatus(ctx, payment, StatusCompleted); err != nil {
			return nil, err
		}
		return payment, nil
	}
	sender, err := loadAccount(ctx, payment.SenderAccountID)
	if err != nil {
		return nil, err
	}
	receiver, err := loadAccount(ctx, payment.ReceiverAccountID)
	if err != nil {
		return nil, err
	}
	if sender.OwnerID != payment.SenderID || receiver.OwnerID != payment.ReceiverID ||
		sender.Currency != payment.Currency || receiver.Currency != payment.Currency {
		return nil, fmt.Errorf("payment account ownership or currency does not match")
	}
	if sender.BalanceMinor < payment.AmountMinor {
		return nil, fmt.Errorf("insufficient ledger balance")
	}
	if receiver.BalanceMinor > math.MaxInt64-payment.AmountMinor {
		return nil, fmt.Errorf("receiver ledger balance would overflow")
	}
	sender.BalanceMinor -= payment.AmountMinor
	receiver.BalanceMinor += payment.AmountMinor
	if err := saveAccount(ctx, sender); err != nil {
		return nil, err
	}
	if err := saveAccount(ctx, receiver); err != nil {
		return nil, err
	}
	if err := setStatus(ctx, payment, StatusCompleted); err != nil {
		return nil, err
	}
	return payment, nil
}

func (c *PaymentContract) RejectPayment(ctx contractapi.TransactionContextInterface, transactionID string) (*PaymentTransaction, error) {
	if err := requireAuthorizedOperator(ctx); err != nil {
		return nil, err
	}
	payment, err := loadPayment(ctx, transactionID)
	if err != nil {
		return nil, err
	}
	if payment.Status == StatusRiskAssessed {
		if payment.PolicyDecision != "REJECT" {
			return nil, fmt.Errorf("policy decision %q does not permit rejection", payment.PolicyDecision)
		}
	} else if payment.Status != StatusHeld && payment.Status != StatusVerificationRequired {
		return nil, invalidTransition(payment.Status, StatusRejected)
	}
	if err := setStatus(ctx, payment, StatusRejected); err != nil {
		return nil, err
	}
	return payment, nil
}

func (c *PaymentContract) GetPayment(ctx contractapi.TransactionContextInterface, transactionID string) (*PaymentTransaction, error) {
	return loadPayment(ctx, transactionID)
}

func (c *PaymentContract) GetAccount(ctx contractapi.TransactionContextInterface, accountID string) (*PaymentAccount, error) {
	return loadAccount(ctx, accountID)
}

func (c *PaymentContract) GetPaymentHistory(ctx contractapi.TransactionContextInterface, transactionID string) ([]*PaymentHistoryEntry, error) {
	transactionID = strings.TrimSpace(transactionID)
	if transactionID == "" {
		return nil, fmt.Errorf("transaction ID cannot be empty")
	}
	startKey := paymentHistoryKeyPrefix + transactionID + ":"
	iterator, err := ctx.GetStub().GetStateByRange(startKey, startKey+"\uffff")
	if err != nil {
		return nil, fmt.Errorf("read payment history: %w", err)
	}
	defer iterator.Close()
	entries := make([]*PaymentHistoryEntry, 0)
	for iterator.HasNext() {
		item, err := iterator.Next()
		if err != nil {
			return nil, fmt.Errorf("read next history entry: %w", err)
		}
		var entry PaymentHistoryEntry
		if err := json.Unmarshal(item.Value, &entry); err != nil {
			return nil, fmt.Errorf("decode payment history: %w", err)
		}
		entries = append(entries, &entry)
	}
	sort.Slice(entries, func(i, j int) bool {
		return entries[i].Sequence < entries[j].Sequence
	})
	return entries, nil
}

func (c *PaymentContract) GetAllPayments(ctx contractapi.TransactionContextInterface) ([]*PaymentTransaction, error) {
	iterator, err := ctx.GetStub().GetStateByRange(paymentKeyPrefix, paymentKeyPrefix+"\uffff")
	if err != nil {
		return nil, fmt.Errorf("list payments: %w", err)
	}
	defer iterator.Close()
	payments := make([]*PaymentTransaction, 0)
	for iterator.HasNext() {
		item, err := iterator.Next()
		if err != nil {
			return nil, fmt.Errorf("read next payment: %w", err)
		}
		var payment PaymentTransaction
		if err := json.Unmarshal(item.Value, &payment); err != nil {
			return nil, fmt.Errorf("decode payment: %w", err)
		}
		payments = append(payments, &payment)
	}
	return payments, nil
}

func loadPayment(ctx contractapi.TransactionContextInterface, transactionID string) (*PaymentTransaction, error) {
	transactionID = strings.TrimSpace(transactionID)
	if transactionID == "" {
		return nil, fmt.Errorf("transaction ID cannot be empty")
	}
	value, err := ctx.GetStub().GetState(paymentKey(transactionID))
	if err != nil {
		return nil, fmt.Errorf("read payment: %w", err)
	}
	if value == nil {
		return nil, fmt.Errorf("payment %q does not exist", transactionID)
	}
	var payment PaymentTransaction
	if err := json.Unmarshal(value, &payment); err != nil {
		return nil, fmt.Errorf("decode payment: %w", err)
	}
	return &payment, nil
}

func createPaymentState(
	ctx contractapi.TransactionContextInterface,
	transactionID, senderID, receiverID, senderAccountID, receiverAccountID string,
	amount, amountMinor int64,
	currency string,
) (*PaymentTransaction, error) {
	now, err := transactionTime(ctx)
	if err != nil {
		return nil, err
	}
	payment := &PaymentTransaction{
		TransactionID:     transactionID,
		SenderID:          senderID,
		ReceiverID:        receiverID,
		SenderAccountID:   senderAccountID,
		ReceiverAccountID: receiverAccountID,
		Amount:            amount,
		AmountDecimal:     fmt.Sprintf("%d.%02d", amountMinor/100, amountMinor%100),
		AmountMinor:       amountMinor,
		Currency:          currency,
		RiskLevel:         "UNASSESSED",
		PolicyDecision:    "UNASSESSED",
		Status:            StatusCreated,
		CreatedAt:         now,
		UpdatedAt:         now,
	}
	if err := putPayment(ctx, paymentKey(transactionID), payment); err != nil {
		return nil, err
	}
	return payment, nil
}

func requireAuthorizedOperator(ctx contractapi.TransactionContextInterface) error {
	identity := ctx.GetClientIdentity()
	if identity == nil {
		return fmt.Errorf("payment mutation requires an authenticated DRUNIX operator")
	}
	mspID, err := identity.GetMSPID()
	if err != nil {
		return fmt.Errorf("read operator MSP identity: %w", err)
	}
	certificate, err := identity.GetX509Certificate()
	if err != nil || certificate == nil {
		return fmt.Errorf("payment mutation requires an X.509 DRUNIX operator identity")
	}
	allowedCommonName := map[string]string{
		"Org1MSP": "Admin@org1.example.com",
		"Org2MSP": "Admin@org2.example.com",
	}[mspID]
	if allowedCommonName == "" || certificate.Subject.CommonName != allowedCommonName {
		return fmt.Errorf("payment mutation is restricted to the configured DRUNIX organization administrators")
	}
	return nil
}

func ensureAccount(
	ctx contractapi.TransactionContextInterface,
	accountID, ownerID string,
	initialBalanceMinor int64,
	currency string,
) (*PaymentAccount, error) {
	key := accountKey(accountID)
	value, err := ctx.GetStub().GetState(key)
	if err != nil {
		return nil, fmt.Errorf("read account: %w", err)
	}
	if value != nil {
		var account PaymentAccount
		if err := json.Unmarshal(value, &account); err != nil {
			return nil, fmt.Errorf("decode account: %w", err)
		}
		if account.OwnerID != ownerID || account.Currency != currency {
			return nil, fmt.Errorf("account identity or currency does not match")
		}
		return &account, nil
	}
	account := &PaymentAccount{
		AccountID:    accountID,
		OwnerID:      ownerID,
		BalanceMinor: initialBalanceMinor,
		Currency:     currency,
	}
	if err := saveAccount(ctx, account); err != nil {
		return nil, err
	}
	return account, nil
}

func loadAccount(ctx contractapi.TransactionContextInterface, accountID string) (*PaymentAccount, error) {
	accountID = strings.TrimSpace(accountID)
	if accountID == "" {
		return nil, fmt.Errorf("account ID cannot be empty")
	}
	value, err := ctx.GetStub().GetState(accountKey(accountID))
	if err != nil {
		return nil, fmt.Errorf("read account: %w", err)
	}
	if value == nil {
		return nil, fmt.Errorf("account %q does not exist", accountID)
	}
	var account PaymentAccount
	if err := json.Unmarshal(value, &account); err != nil {
		return nil, fmt.Errorf("decode account: %w", err)
	}
	return &account, nil
}

func saveAccount(ctx contractapi.TransactionContextInterface, account *PaymentAccount) error {
	value, err := json.Marshal(account)
	if err != nil {
		return fmt.Errorf("encode account: %w", err)
	}
	if err := ctx.GetStub().PutState(accountKey(account.AccountID), value); err != nil {
		return fmt.Errorf("write account: %w", err)
	}
	return nil
}

func setStatus(ctx contractapi.TransactionContextInterface, payment *PaymentTransaction, status PaymentStatus) error {
	now, err := transactionTime(ctx)
	if err != nil {
		return err
	}
	payment.Status = status
	payment.UpdatedAt = now
	return persistPayment(ctx, payment)
}

func setPaymentStatusWithSequence(ctx contractapi.TransactionContextInterface, payment *PaymentTransaction, status PaymentStatus, sequence uint64) error {
	now, err := transactionTime(ctx)
	if err != nil {
		return err
	}
	payment.Status = status
	payment.UpdatedAt = now
	return persistPaymentWithSequence(ctx, payment, sequence)
}

func persistPayment(ctx contractapi.TransactionContextInterface, payment *PaymentTransaction) error {
	return putPayment(ctx, paymentKey(payment.TransactionID), payment)
}

func persistPaymentWithSequence(ctx contractapi.TransactionContextInterface, payment *PaymentTransaction, sequence uint64) error {
	value, err := json.Marshal(payment)
	if err != nil {
		return fmt.Errorf("encode payment: %w", err)
	}
	if err := ctx.GetStub().PutState(paymentKey(payment.TransactionID), value); err != nil {
		return fmt.Errorf("write payment: %w", err)
	}
	entry := &PaymentHistoryEntry{
		TransactionID: payment.TransactionID,
		Sequence:      sequence,
		Timestamp:     payment.UpdatedAt,
		Status:        payment.Status,
		Payment:       *payment,
	}
	entryValue, err := json.Marshal(entry)
	if err != nil {
		return fmt.Errorf("encode payment history: %w", err)
	}
	entryKey := fmt.Sprintf("%s%s:%020d", paymentHistoryKeyPrefix, payment.TransactionID, sequence)
	if err := ctx.GetStub().PutState(entryKey, entryValue); err != nil {
		return fmt.Errorf("write payment history: %w", err)
	}
	countKey := paymentHistoryCountKeyPrefix + payment.TransactionID
	if err := ctx.GetStub().PutState(countKey, []byte(fmt.Sprintf("%d", sequence))); err != nil {
		return fmt.Errorf("write payment history sequence: %w", err)
	}
	return nil
}

func putPayment(ctx contractapi.TransactionContextInterface, key string, payment *PaymentTransaction) error {
	value, err := json.Marshal(payment)
	if err != nil {
		return fmt.Errorf("encode payment: %w", err)
	}
	if err := ctx.GetStub().PutState(key, value); err != nil {
		return fmt.Errorf("write payment: %w", err)
	}
	if err := appendPaymentHistory(ctx, payment); err != nil {
		return err
	}
	return nil
}

func appendPaymentHistory(ctx contractapi.TransactionContextInterface, payment *PaymentTransaction) error {
	countKey := paymentHistoryCountKeyPrefix + payment.TransactionID
	countBytes, err := ctx.GetStub().GetState(countKey)
	if err != nil {
		return fmt.Errorf("read payment history sequence: %w", err)
	}
	var sequence uint64
	if len(countBytes) > 0 {
		if _, err := fmt.Sscanf(string(countBytes), "%d", &sequence); err != nil {
			return fmt.Errorf("decode payment history sequence: %w", err)
		}
	}
	sequence++
	entry := &PaymentHistoryEntry{
		TransactionID: payment.TransactionID,
		Sequence:      sequence,
		Timestamp:     payment.UpdatedAt,
		Status:        payment.Status,
		Payment:       *payment,
	}
	value, err := json.Marshal(entry)
	if err != nil {
		return fmt.Errorf("encode payment history: %w", err)
	}
	entryKey := fmt.Sprintf("%s%s:%020d", paymentHistoryKeyPrefix, payment.TransactionID, sequence)
	if err := ctx.GetStub().PutState(entryKey, value); err != nil {
		return fmt.Errorf("write payment history: %w", err)
	}
	if err := ctx.GetStub().PutState(countKey, []byte(fmt.Sprintf("%d", sequence))); err != nil {
		return fmt.Errorf("write payment history sequence: %w", err)
	}
	return nil
}

func transactionTime(ctx contractapi.TransactionContextInterface) (string, error) {
	timestamp, err := ctx.GetStub().GetTxTimestamp()
	if err != nil {
		return "", fmt.Errorf("get transaction timestamp: %w", err)
	}
	if timestamp == nil {
		return "", fmt.Errorf("invalid transaction timestamp")
	}
	if err := timestamp.CheckValid(); err != nil {
		return "", fmt.Errorf("invalid transaction timestamp: %w", err)
	}
	return timestamp.AsTime().UTC().Format(time.RFC3339Nano), nil
}

func paymentKey(transactionID string) string {
	return paymentKeyPrefix + transactionID
}

func accountKey(accountID string) string {
	return accountKeyPrefix + accountID
}

func policyForRisk(score float64) (string, string) {
	switch {
	case score < 0.30:
		return "LOW", "APPROVE"
	case score < 0.70:
		return "MEDIUM", "VERIFY"
	default:
		return "HIGH", "HOLD"
	}
}

func validateRiskAssessment(score float64, riskLevel, policyDecision string) (string, string, error) {
	if math.IsNaN(score) || math.IsInf(score, 0) || score < 0 || score > 1 {
		return "", "", fmt.Errorf("risk score must be finite and between 0 and 1")
	}
	riskLevel = strings.ToUpper(strings.TrimSpace(riskLevel))
	if riskLevel != "LOW" && riskLevel != "MEDIUM" && riskLevel != "HIGH" {
		return "", "", fmt.Errorf("risk level must be LOW, MEDIUM, or HIGH")
	}
	policyDecision = strings.ToUpper(strings.TrimSpace(policyDecision))
	if policyDecision != "APPROVE" && policyDecision != "HOLD" && policyDecision != "VERIFY" && policyDecision != "REJECT" {
		return "", "", fmt.Errorf("policy decision must be APPROVE, HOLD, VERIFY, or REJECT")
	}
	expectedLevel, expectedDecision := policyForRisk(score)
	if riskLevel != expectedLevel {
		return "", "", fmt.Errorf("risk score %.2f requires risk level %s", score, expectedLevel)
	}
	if policyDecision != "REJECT" && policyDecision != expectedDecision {
		return "", "", fmt.Errorf("risk level %s requires policy decision %s", riskLevel, expectedDecision)
	}
	return riskLevel, policyDecision, nil
}

func invalidTransition(from, to PaymentStatus) error {
	return fmt.Errorf("invalid payment state transition: %s -> %s", from, to)
}
