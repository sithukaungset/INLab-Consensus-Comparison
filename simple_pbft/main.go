package main

import (
	"fmt"
	"os"
	"time"

	"github.com/bigpicturelabsinc/consensusPBFT/pbft/network"
)

func main() {
	now := time.Now()
	defer func() {
		fmt.Println(time.Since(now))
	}()
	nodeID := os.Args[1]
	server := network.NewServer(nodeID)

	server.Start()
}
