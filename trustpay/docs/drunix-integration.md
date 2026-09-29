# DRUNIX Integration

## Existing verified environment

The adjacent official NPCI DRUNIX v1.0.0 checkout and its Ubuntu 24.04 WSL2 test network are used on channel `mychannel`. The official `npcioss/drunix-ccenv:1.0` image is required by the network's peer builder configuration. The network is real; the chaincode and ledger are real. Payment participants and amounts remain synthetic.

The official DRUNIX test network's `prereq` command installs Fabric-compatible CLI binaries and standard Fabric images; it does not obtain the DRUNIX-specific chaincode builder. The exact DRUNIX builder image must be present for lifecycle installation.

## Deployment procedure

From `../drunix/drunix-network/test-network`, with `../drunix/drunix-network/bin` on `PATH`:

```bash
./network.sh deployCC -c mychannel -ccn trustpay -ccp ../../../trustpay/chaincode/trustpay -ccl go -ccv 1.0 -ccs auto
./network.sh cc list -org 1
./network.sh cc list -org 2
```

Paths are relative to the test-network directory. Confirm both organizations report the committed chaincode name, version, and sequence before submitting transactions.

Invoke and query requests use the test-network wrapper's JSON constructors, for example:

```bash
./network.sh cc invoke -c mychannel -ccn trustpay -ccic '{"Args":["CreatePayment","txn-001","user-001","merchant-001","2500","INR"]}'
./network.sh cc query -c mychannel -ccn trustpay -ccqc '{"Args":["GetPayment","txn-001"]}'
./network.sh cc query -c mychannel -ccn trustpay -ccqc '{"Args":["GetPaymentHistory","txn-001"]}'
```

`GetPaymentHistory` reads the chaincode's explicit append-only ledger records. Each successful state-changing function updates the payment and adds a history snapshot atomically; failed transitions do neither.

Run these commands in a Bash shell (Ubuntu WSL), quote the JSON as one shell argument, and inspect the exact peer command/output. Do not infer transaction IDs that the CLI does not print. Transaction function argument schemas are specified by the exported methods in the contract; this example illustrates the wrapper pattern and must be kept aligned with that schema.

## Future FastAPI client choice

The official DRUNIX sample includes Fabric Gateway clients (including Go). The currently verified official client route is Gateway over gRPC using a network identity, private key, TLS certificate, and the channel/chaincode contract. There is no evidenced official Python DRUNIX Gateway SDK in this repository. The smallest real backend integration should therefore be a small Go Gateway sidecar/service using the official Gateway library, called by FastAPI over a narrow local API, or a carefully evaluated supported Fabric Gateway binding. Do not invent a Python DRUNIX API or silently fall back to a mock.

## Limits

The contract records policy/state, not payment settlement. No UPI/NPCI production rails or real funds are involved. The version-1 contract's amount representation is integer whole INR units.