package main

import (
	"log"

	"github.com/hyperledger/fabric-contract-api-go/v2/contractapi"
)

func main() {
	chaincode, err := contractapi.NewChaincode(&PaymentContract{})
	if err != nil {
		log.Fatalf("create ShariPay chaincode: %v", err)
	}
	if err := chaincode.Start(); err != nil {
		log.Fatalf("start ShariPay chaincode: %v", err)
	}
}
