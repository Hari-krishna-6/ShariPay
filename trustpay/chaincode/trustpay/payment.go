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
	Amount               int64         `json:"amount"`
	Currency             string        `json:"currency"`
	RiskScore            float64       `json:"riskScore"`
	RiskLevel            string        `json:"riskLevel"`
	PolicyDecision       string        `json:"policyDecision"`
	Status               PaymentStatus `json:"status"`
	CreatedAt            string        `json:"createdAt"`
	UpdatedAt            string        `json:"updatedAt"`
	VerificationRequired bool          `json:"verificationRequired"`
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

	now, err := transactionTime(ctx)
	if err != nil {
		return nil, err
	}
	payment := &PaymentTransaction{
		TransactionID:  transactionID,
		SenderID:       senderID,
		ReceiverID:     receiverID,
		Amount:         amount,
		Currency:       currency,
		RiskLevel:      "UNASSESSED",
		PolicyDecision: "UNASSESSED",
		Status:         StatusCreated,
		CreatedAt:      now,
		UpdatedAt:      now,
	}
	if err := putPayment(ctx, key, payment); err != nil {
		return nil, err
	}
	return payment, nil
}

func (c *PaymentContract) AssessRisk(ctx contractapi.TransactionContextInterface, transactionID string, riskScore float64, riskLevel, policyDecision string) (*PaymentTransaction, error) {
	if math.IsNaN(riskScore) || math.IsInf(riskScore, 0) || riskScore < 0 || riskScore > 1 {
		return nil, fmt.Errorf("risk score must be finite and between 0 and 1")
	}
	riskLevel = strings.ToUpper(strings.TrimSpace(riskLevel))
	if riskLevel != "LOW" && riskLevel != "MEDIUM" && riskLevel != "HIGH" {
		return nil, fmt.Errorf("risk level must be LOW, MEDIUM, or HIGH")
	}
	policyDecision = strings.ToUpper(strings.TrimSpace(policyDecision))
	if policyDecision != "APPROVE" && policyDecision != "HOLD" && policyDecision != "VERIFY" && policyDecision != "REJECT" {
		return nil, fmt.Errorf("policy decision must be APPROVE, HOLD, VERIFY, or REJECT")
	}
	expectedLevel, expectedDecision := policyForRisk(riskScore)
	if riskLevel != expectedLevel {
		return nil, fmt.Errorf("risk score %.2f requires risk level %s", riskScore, expectedLevel)
	}
	if policyDecision != "REJECT" && policyDecision != expectedDecision {
		return nil, fmt.Errorf("risk level %s requires policy decision %s", riskLevel, expectedDecision)
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
	payment, err := loadPayment(ctx, transactionID)
	if err != nil {
		return nil, err
	}
	if payment.Status != StatusApproved {
		return nil, invalidTransition(payment.Status, StatusCompleted)
	}
	if err := setStatus(ctx, payment, StatusCompleted); err != nil {
		return nil, err
	}
	return payment, nil
}

func (c *PaymentContract) RejectPayment(ctx contractapi.TransactionContextInterface, transactionID string) (*PaymentTransaction, error) {
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

func setStatus(ctx contractapi.TransactionContextInterface, payment *PaymentTransaction, status PaymentStatus) error {
	now, err := transactionTime(ctx)
	if err != nil {
		return err
	}
	payment.Status = status
	payment.UpdatedAt = now
	return persistPayment(ctx, payment)
}

func persistPayment(ctx contractapi.TransactionContextInterface, payment *PaymentTransaction) error {
	return putPayment(ctx, paymentKey(payment.TransactionID), payment)
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

func invalidTransition(from, to PaymentStatus) error {
	return fmt.Errorf("invalid payment state transition: %s -> %s", from, to)
}
