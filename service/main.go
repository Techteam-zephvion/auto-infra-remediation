package main

import (
	"fmt"
	"log"
	"net/http"
	"os"
	"runtime"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

var (
	requestCount = promauto.NewCounterVec(
		prometheus.CounterOpts{
			Name: "http_requests_total",
			Help: "Total number of HTTP requests processed",
		},
		[]string{"path", "status"},
	)
	
	// Track leaked memory slices
	leakedMemory [][]byte
	
	// To prevent garbage collection of leaked memory
	leakerDone chan bool
)

func recordMetrics(path string, status string) {
	requestCount.WithLabelValues(path, status).Inc()
}

func main() {
	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}

	http.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		recordMetrics("/", "200")
		fmt.Fprintf(w, "Failure Simulator Running\n")
	})

	// 1. CPU Spike
	http.HandleFunc("/spike-cpu", func(w http.ResponseWriter, r *http.Request) {
		recordMetrics("/spike-cpu", "200")
		fmt.Fprintf(w, "Triggering CPU Spike for 60 seconds...\n")
		
		go func() {
			end := time.Now().Add(60 * time.Second)
			for time.Now().Before(end) {
				// busy loop
				x := 1
				_ = x + 1
			}
			fmt.Println("CPU spike ended.")
		}()
	})

	// 2. Memory Leak
	http.HandleFunc("/leak-memory", func(w http.ResponseWriter, r *http.Request) {
		recordMetrics("/leak-memory", "200")
		fmt.Fprintf(w, "Allocating 50MB of memory...\n")
		
		go func() {
			// Allocate 50MB
			chunk := make([]byte, 50*1024*1024)
			for i := range chunk {
				chunk[i] = 1 // Prevent optimization
			}
			leakedMemory = append(leakedMemory, chunk)
			
			var m runtime.MemStats
			runtime.ReadMemStats(&m)
			fmt.Printf("Allocated Memory: %v MB\n", m.Alloc/1024/1024)
		}()
	})
	
	// Helper to free memory
	http.HandleFunc("/free-memory", func(w http.ResponseWriter, r *http.Request) {
		recordMetrics("/free-memory", "200")
		leakedMemory = nil // Allow GC to reclaim
		runtime.GC()
		fmt.Fprintf(w, "Memory freed.\n")
	})

	// 3. HTTP 500
	http.HandleFunc("/error-500", func(w http.ResponseWriter, r *http.Request) {
		recordMetrics("/error-500", "500")
		w.WriteHeader(http.StatusInternalServerError)
		fmt.Fprintf(w, "Internal Server Error\n")
	})

	// Prometheus Metrics Endpoint
	http.Handle("/metrics", promhttp.Handler())

	fmt.Printf("Starting server on port %s...\n", port)
	log.Fatal(http.ListenAndServe(":"+port, nil))
}
