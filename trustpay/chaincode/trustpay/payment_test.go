package main

import (
	"encoding/json"
	"fmt"
	"math"
	"sort"
	"testing"
	"time"

	"github.com/hyperledger/fabric-chaincode-go/v2/shim"
	"github.com/hyperledger/fabric-contract-api-go/v2/contractapi"
	"github.com/hyperledger/fabric-protos-go-apiv2/ledger/queryresult"
	"github.com/stretchr/testify/require"
	"google.golang.org/protobuf/types/known/timestamppb"
)

type testContext struct {
	contractapi.TransactionContextInterface
	stub *memoryStub
}

func (c *testContext) GetStub() shim.ChaincodeStubInterface {
	return c.stub
}

type memoryStub struct {
	shim.ChaincodeStubInterface
	state     map[string][]byte
	history   map[string][]*queryresult.KeyModification
	timestamp *timestamppb.Timestamp
}

func newTestContext() *testContext {
	return &testContext{stub: &memoryStub{
		state:     make(map[string][]byte),
		history:   make(map[string][]*queryresult.KeyModification),
		timestamp: timestamppb.New(time.Date(2026, 9, 29, 12, 0, 0, 0, time.UTC)),
	}}
}

func (s *memoryStub) GetState(key string) ([]byte, error) {
	value, exists := s.state[key]
	if !exists {
		return nil, nil
	}
	return append([]byte(nil), value...), nil
}

func (s *memoryStub) PutState(key string, value []byte) error {
	s.state[key] = append([]byte(nil), value...)
	s.history[key] = append(s.history[key], &queryresult.KeyModification{
		TxId:      fmt.Sprintf("test-tx-%d", len(s.history[key])+1),
		Value:     append([]byte(nil), value...),
		Timestamp: s.timestamp,
	})
	return nil
}

func (s *memoryStub) GetTxTimestamp() (*timestamppb.Timestamp, error) {
	return s.timestamp, nil
}

func (s *memoryStub) GetStateByRange(startKey, endKey string) (shim.StateQueryIteratorInterface, error) {
	keys := make([]string, 0, len(s.state))
	for key := range s.state {
		if key >= startKey && (endKey == "" || key < endKey) {
			keys = append(keys, key)
		}
	}
	sort.Strings(keys)
	items := make([]*queryresult.KV, 0, len(keys))
	for _, key := range keys {
		items = append(items, &queryresult.KV{Key: key, Value: append([]byte(nil), s.state[key]...)})
	}
	return &stateIterator{items: items}, nil
}

func (s *memoryStub) GetHistoryForKey(key string) (shim.HistoryQueryIteratorInterface, error) {
	return &historyIterator{items: s.history[key]}, nil
}

type stateIterator struct {
	items []*queryresult.KV
	index int
}

func (i *stateIterator) HasNext() bool { return i.index < len(i.items) }
func (i *stateIterator) Close() error  { return nil }
func (i *stateIterator) Next() (*queryresult.KV, error) {
	if !i.HasNext() {
		return nil, fmt.Errorf("iterator exhausted")
	}
	item := i.items[i.index]
	i.index++
	return item, nil
}

type historyIterator struct {
	items []*queryresult.KeyModification
	index int
}

func (i *historyIterator) HasNext() bool { return i.index < len(i.items) }
func (i *historyIterator) Close() error  { return nil }
func (i *historyIterator) Next() (*queryresult.KeyModification, error) {
	if !i.HasNext() {
		return nil, fmt.Errorf("iterator exhausted")
	}
	item := i.items[i.index]
	i.index++
	return item, nil
}

func TestCreatePaymentValidationAndDuplicate(t *testing.T) {
	t.Run("valid create", func(t *testing.T) {
		contract := &PaymentContract{}
		ctx := newTestContext()
		payment, err := contract.CreatePayment(ctx, "txn-1", "user-1", "merchant-1", 2500, "INR")
		require.NoError(t, err)
		require.Equal(t, StatusCreated, payment.Status)
		require.Equal(t, "UNASSESSED", payment.RiskLevel)
		require.Equal(t, "UNASSESSED", payment.PolicyDecision)
		require.Equal(t, "2026-09-29T12:00:00Z", payment.CreatedAt)
		require.Equal(t, payment.CreatedAt, payment.UpdatedAt)
	})
	t.Run("duplicate id", func(t *testing.T) {
		contract := &PaymentContract{}
		ctx := newTestContext()
		_, err := contract.CreatePayment(ctx, "txn-1", "user-1", "merchant-1", 2500, "INR")
		require.NoError(t, err)
		_, err = contract.CreatePayment(ctx, "txn-1", "user-1", "merchant-1", 2500, "INR")
		require.EqualError(t, err, `payment "txn-1" already exists`)
	})
	invalid := []struct {
		name, id, sender, receiver string
		amount                     int64
		currency                   string
	}{
		{name: "empty transaction id", id: " ", sender: "user-1", receiver: "merchant-1", amount: 1, currency: "INR"},
		{name: "empty sender", id: "txn", sender: " ", receiver: "merchant-1", amount: 1, currency: "INR"},
		{name: "empty receiver", id: "txn", sender: "user-1", receiver: " ", amount: 1, currency: "INR"},
		{name: "non-positive amount", id: "txn", sender: "user-1", receiver: "merchant-1", amount: 0, currency: "INR"},
		{name: "invalid currency", id: "txn", sender: "user-1", receiver: "merchant-1", amount: 1, currency: "inr"},
	}
	for _, test := range invalid {
		t.Run(test.name, func(t *testing.T) {
			_, err := (&PaymentContract{}).CreatePayment(newTestContext(), test.id, test.sender, test.receiver, test.amount, test.currency)
			require.Error(t, err)
		})
	}
}

func TestAssessRiskValidation(t *testing.T) {
	contract := &PaymentContract{}
	ctx := newTestContext()
	_, err := contract.CreatePayment(ctx, "txn-risk", "user-1", "merchant-1", 2500, "INR")
	require.NoError(t, err)
	for _, score := range []float64{-0.01, 1.01, math.NaN()} {
		_, err = contract.AssessRisk(ctx, "txn-risk", score, "LOW", "APPROVE")
		require.Error(t, err)
	}
	_, err = contract.AssessRisk(ctx, "txn-risk", 0.91, "LOW", "APPROVE")
	require.EqualError(t, err, "risk score 0.91 requires risk level HIGH")
	_, err = contract.AssessRisk(ctx, "txn-risk", 0.91, "HIGH", "APPROVE")
	require.EqualError(t, err, "risk level HIGH requires policy decision HOLD")
	payment, err := contract.AssessRisk(ctx, "txn-risk", 0.12, "LOW", "APPROVE")
	require.NoError(t, err)
	require.Equal(t, StatusRiskAssessed, payment.Status)
	require.Equal(t, 0.12, payment.RiskScore)
	require.Equal(t, "APPROVE", payment.PolicyDecision)
}

func TestAssessRiskAllowsRejectForEveryValidRiskLevel(t *testing.T) {
	tests := []struct {
		name      string
		score     float64
		riskLevel string
	}{
		{name: "low", score: 0.15, riskLevel: "LOW"},
		{name: "medium", score: 0.45, riskLevel: "MEDIUM"},
		{name: "high", score: 0.85, riskLevel: "HIGH"},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			contract := &PaymentContract{}
			ctx := newTestContext()
			_, err := contract.CreatePayment(ctx, "reject-"+test.name, "sender", "receiver", 2500, "INR")
			require.NoError(t, err)

			payment, err := contract.AssessRisk(ctx, "reject-"+test.name, test.score, test.riskLevel, "REJECT")
			require.NoError(t, err)
			require.Equal(t, StatusRiskAssessed, payment.Status)
			require.Equal(t, test.score, payment.RiskScore)
			require.Equal(t, test.riskLevel, payment.RiskLevel)
			require.Equal(t, "REJECT", payment.PolicyDecision)

			payment, err = contract.RejectPayment(ctx, "reject-"+test.name)
			require.NoError(t, err)
			require.Equal(t, StatusRejected, payment.Status)
			require.Equal(t, test.score, payment.RiskScore)
			require.Equal(t, test.riskLevel, payment.RiskLevel)
			require.Equal(t, "REJECT", payment.PolicyDecision)

			stored, err := contract.GetPayment(ctx, "reject-"+test.name)
			require.NoError(t, err)
			require.Equal(t, *payment, *stored)

			history, err := contract.GetPaymentHistory(ctx, "reject-"+test.name)
			require.NoError(t, err)
			require.Equal(t, []PaymentStatus{StatusCreated, StatusRiskAssessed, StatusRejected}, []PaymentStatus{
				history[0].Status, history[1].Status, history[2].Status,
			})
			require.Equal(t, []uint64{1, 2, 3}, []uint64{
				history[0].Sequence, history[1].Sequence, history[2].Sequence,
			})
			require.Len(t, history, 3)
		})
	}
}

func TestAssessRiskRejectsNonRiskDerivedCombinations(t *testing.T) {
	tests := []struct {
		name      string
		score     float64
		riskLevel string
		decision  string
	}{
		{name: "low verify", score: 0.15, riskLevel: "LOW", decision: "VERIFY"},
		{name: "low hold", score: 0.15, riskLevel: "LOW", decision: "HOLD"},
		{name: "medium approve", score: 0.45, riskLevel: "MEDIUM", decision: "APPROVE"},
		{name: "medium hold", score: 0.45, riskLevel: "MEDIUM", decision: "HOLD"},
		{name: "high approve", score: 0.85, riskLevel: "HIGH", decision: "APPROVE"},
		{name: "high verify", score: 0.85, riskLevel: "HIGH", decision: "VERIFY"},
	}

	for index, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			contract := &PaymentContract{}
			ctx := newTestContext()
			id := fmt.Sprintf("invalid-pair-%d", index)
			_, err := contract.CreatePayment(ctx, id, "sender", "receiver", 2500, "INR")
			require.NoError(t, err)

			_, err = contract.AssessRisk(ctx, id, test.score, test.riskLevel, test.decision)
			require.EqualError(t, err, fmt.Sprintf("risk level %s requires policy decision %s", test.riskLevel, map[string]string{
				"LOW": "APPROVE", "MEDIUM": "VERIFY", "HIGH": "HOLD",
			}[test.riskLevel]))

			payment, err := contract.GetPayment(ctx, id)
			require.NoError(t, err)
			require.Equal(t, StatusCreated, payment.Status)
			require.Equal(t, "UNASSESSED", payment.RiskLevel)
			require.Equal(t, "UNASSESSED", payment.PolicyDecision)
			history, err := contract.GetPaymentHistory(ctx, id)
			require.NoError(t, err)
			require.Len(t, history, 1)
			require.Equal(t, StatusCreated, history[0].Status)
		})
	}
}

func TestAssessRiskRejectsInvalidRiskAndPolicyValues(t *testing.T) {
	tests := []struct {
		name      string
		score     float64
		riskLevel string
		decision  string
	}{
		{name: "unknown risk level", score: 0.15, riskLevel: "CRITICAL", decision: "REJECT"},
		{name: "unknown decision", score: 0.15, riskLevel: "LOW", decision: "ESCALATE"},
		{name: "empty decision", score: 0.15, riskLevel: "LOW", decision: ""},
	}

	for index, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			contract := &PaymentContract{}
			ctx := newTestContext()
			id := fmt.Sprintf("invalid-value-%d", index)
			_, err := contract.CreatePayment(ctx, id, "sender", "receiver", 2500, "INR")
			require.NoError(t, err)

			_, err = contract.AssessRisk(ctx, id, test.score, test.riskLevel, test.decision)
			require.Error(t, err)

			payment, err := contract.GetPayment(ctx, id)
			require.NoError(t, err)
			require.Equal(t, StatusCreated, payment.Status)
			require.Equal(t, "UNASSESSED", payment.RiskLevel)
			require.Equal(t, "UNASSESSED", payment.PolicyDecision)
			history, err := contract.GetPaymentHistory(ctx, id)
			require.NoError(t, err)
			require.Len(t, history, 1)
		})
	}
}

func TestContractMetadata(t *testing.T) {
	_, err := contractapi.NewChaincode(&PaymentContract{})
	require.NoError(t, err)
}

func TestNormalLifecycle(t *testing.T) {
	contract := &PaymentContract{}
	ctx := newTestContext()
	_, err := contract.CreatePayment(ctx, "normal", "user-001", "merchant-001", 2500, "INR")
	require.NoError(t, err)
	_, err = contract.AssessRisk(ctx, "normal", 0.12, "LOW", "APPROVE")
	require.NoError(t, err)
	_, err = contract.ApprovePayment(ctx, "normal")
	require.NoError(t, err)
	_, err = contract.CompletePayment(ctx, "normal")
	require.NoError(t, err)
	payment, err := contract.GetPayment(ctx, "normal")
	require.NoError(t, err)
	require.Equal(t, StatusCompleted, payment.Status)
	history, err := contract.GetPaymentHistory(ctx, "normal")
	require.NoError(t, err)
	require.Len(t, history, 4)
	require.Equal(t, []PaymentStatus{StatusCreated, StatusRiskAssessed, StatusApproved, StatusCompleted}, []PaymentStatus{
		history[0].Status, history[1].Status, history[2].Status, history[3].Status,
	})
	require.Equal(t, []uint64{1, 2, 3, 4}, []uint64{
		history[0].Sequence, history[1].Sequence, history[2].Sequence, history[3].Sequence,
	})
	all, err := contract.GetAllPayments(ctx)
	require.NoError(t, err)
	require.Len(t, all, 1)
}

func TestPolicyBranchesAndInvalidTransitions(t *testing.T) {
	contract := &PaymentContract{}
	ctx := newTestContext()
	create := func(id string) {
		t.Helper()
		_, err := contract.CreatePayment(ctx, id, "user-1", "merchant-1", 85000, "INR")
		require.NoError(t, err)
	}

	create("held")
	_, err := contract.AssessRisk(ctx, "held", 0.91, "HIGH", "HOLD")
	require.NoError(t, err)
	_, err = contract.HoldPayment(ctx, "held")
	require.NoError(t, err)
	_, err = contract.CompletePayment(ctx, "held")
	require.EqualError(t, err, "invalid payment state transition: HELD -> COMPLETED")
	history, err := contract.GetPaymentHistory(ctx, "held")
	require.NoError(t, err)
	require.Len(t, history, 3)
	_, err = contract.RequestVerification(ctx, "held")
	require.NoError(t, err)
	payment, err := contract.GetPayment(ctx, "held")
	require.NoError(t, err)
	require.Equal(t, StatusVerificationRequired, payment.Status)
	require.True(t, payment.VerificationRequired)
	_, err = contract.ApprovePayment(ctx, "held")
	require.NoError(t, err)
	_, err = contract.CompletePayment(ctx, "held")
	require.NoError(t, err)
	payment, err = contract.GetPayment(ctx, "held")
	require.NoError(t, err)
	require.Equal(t, StatusCompleted, payment.Status)
	require.False(t, payment.VerificationRequired)

	create("verify")
	_, err = contract.AssessRisk(ctx, "verify", 0.55, "MEDIUM", "VERIFY")
	require.NoError(t, err)
	_, err = contract.RequestVerification(ctx, "verify")
	require.NoError(t, err)
	_, err = contract.ApprovePayment(ctx, "verify")
	require.NoError(t, err)

	create("reject")
	_, err = contract.AssessRisk(ctx, "reject", 0.95, "HIGH", "HOLD")
	require.NoError(t, err)
	_, err = contract.HoldPayment(ctx, "reject")
	require.NoError(t, err)
	_, err = contract.RejectPayment(ctx, "reject")
	require.NoError(t, err)
	payment, err = contract.GetPayment(ctx, "reject")
	require.NoError(t, err)
	require.Equal(t, StatusRejected, payment.Status)
}

func TestRejectAndTerminalTransitionGuards(t *testing.T) {
	newPayment := func(id string) (*PaymentContract, *testContext) {
		contract := &PaymentContract{}
		ctx := newTestContext()
		_, err := contract.CreatePayment(ctx, id, "sender", "receiver", 2500, "INR")
		require.NoError(t, err)
		return contract, ctx
	}

	tests := []struct {
		name       string
		setup      func(*PaymentContract, *testContext, string)
		operation  func(*PaymentContract, *testContext, string) error
		wantStatus PaymentStatus
	}{
		{
			name:  "created cannot be rejected",
			setup: func(_ *PaymentContract, _ *testContext, _ string) {},
			operation: func(contract *PaymentContract, ctx *testContext, id string) error {
				_, err := contract.RejectPayment(ctx, id)
				return err
			},
			wantStatus: StatusCreated,
		},
		{
			name: "risk assessed without reject cannot be rejected",
			setup: func(contract *PaymentContract, ctx *testContext, id string) {
				_, err := contract.AssessRisk(ctx, id, 0.15, "LOW", "APPROVE")
				require.NoError(t, err)
			},
			operation: func(contract *PaymentContract, ctx *testContext, id string) error {
				_, err := contract.RejectPayment(ctx, id)
				return err
			},
			wantStatus: StatusRiskAssessed,
		},
		{
			name: "approved cannot be rejected",
			setup: func(contract *PaymentContract, ctx *testContext, id string) {
				_, err := contract.AssessRisk(ctx, id, 0.15, "LOW", "APPROVE")
				require.NoError(t, err)
				_, err = contract.ApprovePayment(ctx, id)
				require.NoError(t, err)
			},
			operation: func(contract *PaymentContract, ctx *testContext, id string) error {
				_, err := contract.RejectPayment(ctx, id)
				return err
			},
			wantStatus: StatusApproved,
		},
		{
			name: "completed cannot be rejected",
			setup: func(contract *PaymentContract, ctx *testContext, id string) {
				_, err := contract.AssessRisk(ctx, id, 0.15, "LOW", "APPROVE")
				require.NoError(t, err)
				_, err = contract.ApprovePayment(ctx, id)
				require.NoError(t, err)
				_, err = contract.CompletePayment(ctx, id)
				require.NoError(t, err)
			},
			operation: func(contract *PaymentContract, ctx *testContext, id string) error {
				_, err := contract.RejectPayment(ctx, id)
				return err
			},
			wantStatus: StatusCompleted,
		},
		{
			name: "held cannot be completed",
			setup: func(contract *PaymentContract, ctx *testContext, id string) {
				_, err := contract.AssessRisk(ctx, id, 0.85, "HIGH", "HOLD")
				require.NoError(t, err)
				_, err = contract.HoldPayment(ctx, id)
				require.NoError(t, err)
			},
			operation: func(contract *PaymentContract, ctx *testContext, id string) error {
				_, err := contract.CompletePayment(ctx, id)
				return err
			},
			wantStatus: StatusHeld,
		},
		{
			name: "verification required cannot be completed",
			setup: func(contract *PaymentContract, ctx *testContext, id string) {
				_, err := contract.AssessRisk(ctx, id, 0.45, "MEDIUM", "VERIFY")
				require.NoError(t, err)
				_, err = contract.RequestVerification(ctx, id)
				require.NoError(t, err)
			},
			operation: func(contract *PaymentContract, ctx *testContext, id string) error {
				_, err := contract.CompletePayment(ctx, id)
				return err
			},
			wantStatus: StatusVerificationRequired,
		},
		{
			name: "rejected cannot be completed",
			setup: func(contract *PaymentContract, ctx *testContext, id string) {
				_, err := contract.AssessRisk(ctx, id, 0.15, "LOW", "REJECT")
				require.NoError(t, err)
				_, err = contract.RejectPayment(ctx, id)
				require.NoError(t, err)
			},
			operation: func(contract *PaymentContract, ctx *testContext, id string) error {
				_, err := contract.CompletePayment(ctx, id)
				return err
			},
			wantStatus: StatusRejected,
		},
	}

	for index, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			id := fmt.Sprintf("transition-guard-%d", index)
			contract, ctx := newPayment(id)
			test.setup(contract, ctx, id)
			before, err := contract.GetPayment(ctx, id)
			require.NoError(t, err)
			beforeHistory, err := contract.GetPaymentHistory(ctx, id)
			require.NoError(t, err)

			err = test.operation(contract, ctx, id)
			require.Error(t, err)
			if test.name == "risk assessed without reject cannot be rejected" {
				require.EqualError(t, err, `policy decision "APPROVE" does not permit rejection`)
			}

			after, err := contract.GetPayment(ctx, id)
			require.NoError(t, err)
			require.Equal(t, test.wantStatus, after.Status)
			require.Equal(t, before, after)
			afterHistory, err := contract.GetPaymentHistory(ctx, id)
			require.NoError(t, err)
			require.Equal(t, beforeHistory, afterHistory)
		})
	}

	contract, ctx := newPayment("held-reject")
	_, err := contract.AssessRisk(ctx, "held-reject", 0.85, "HIGH", "HOLD")
	require.NoError(t, err)
	_, err = contract.HoldPayment(ctx, "held-reject")
	require.NoError(t, err)
	_, err = contract.RejectPayment(ctx, "held-reject")
	require.NoError(t, err)

	contract, ctx = newPayment("verification-reject")
	_, err = contract.AssessRisk(ctx, "verification-reject", 0.45, "MEDIUM", "VERIFY")
	require.NoError(t, err)
	_, err = contract.RequestVerification(ctx, "verification-reject")
	require.NoError(t, err)
	_, err = contract.RejectPayment(ctx, "verification-reject")
	require.NoError(t, err)

	contract, ctx = newPayment("rejected-again")
	_, err = contract.AssessRisk(ctx, "rejected-again", 0.15, "LOW", "REJECT")
	require.NoError(t, err)
	_, err = contract.RejectPayment(ctx, "rejected-again")
	require.NoError(t, err)
	before, err := contract.GetPayment(ctx, "rejected-again")
	require.NoError(t, err)
	beforeHistory, err := contract.GetPaymentHistory(ctx, "rejected-again")
	require.NoError(t, err)
	_, err = contract.RejectPayment(ctx, "rejected-again")
	require.EqualError(t, err, "invalid payment state transition: REJECTED -> REJECTED")
	after, err := contract.GetPayment(ctx, "rejected-again")
	require.NoError(t, err)
	require.Equal(t, before, after)
	afterHistory, err := contract.GetPaymentHistory(ctx, "rejected-again")
	require.NoError(t, err)
	require.Equal(t, beforeHistory, afterHistory)
}

func TestNonexistentPaymentIsRejected(t *testing.T) {
	contract := &PaymentContract{}
	ctx := newTestContext()
	operations := []func() error{
		func() error { _, err := contract.AssessRisk(ctx, "missing", 0.2, "LOW", "APPROVE"); return err },
		func() error { _, err := contract.ApprovePayment(ctx, "missing"); return err },
		func() error { _, err := contract.HoldPayment(ctx, "missing"); return err },
		func() error { _, err := contract.RequestVerification(ctx, "missing"); return err },
		func() error { _, err := contract.CompletePayment(ctx, "missing"); return err },
		func() error { _, err := contract.RejectPayment(ctx, "missing"); return err },
	}
	for _, operation := range operations {
		require.Error(t, operation())
	}
}

func TestPaymentJSONSerialization(t *testing.T) {
	contract := &PaymentContract{}
	ctx := newTestContext()
	created, err := contract.CreatePayment(ctx, "json", "user", "merchant", 1, "INR")
	require.NoError(t, err)
	encoded, err := json.Marshal(created)
	require.NoError(t, err)
	var decoded PaymentTransaction
	require.NoError(t, json.Unmarshal(encoded, &decoded))
	require.Equal(t, *created, decoded)
}
