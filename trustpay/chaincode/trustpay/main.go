package main

import (
	"log"

	"github.com/hyperledger/fabric-contract-api-go/v2/contractapi"
)

func main() {
	chaincode, err := contractapi.NewChaincode(&PaymentContract{})
	if err != nil {
		log.Fatalf("create TrustPay chaincode: %v", err)
	}
	if err := chaincode.Start(); err != nil {
		log.Fatalf("start TrustPay chaincode: %v", err)
	}
}
