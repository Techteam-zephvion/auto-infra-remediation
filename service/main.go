package main

import (
	"fmt"
	"log/slog"
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
)

// Structured logger — outputs JSON-compatible key=value pairs
var logger = slog.New(slog.NewTextHandler(os.Stdout, &slog.HandlerOptions{
	Level: slog.LevelDebug,
}))

func recordMetrics(path string, status string) {
	requestCount.WithLabelValues(path, status).Inc()
}

// httpLogger wraps a handler to log every request with method, path, status, and duration
func httpLogger(path string, statusCode string, handler http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		handler(w, r)
		elapsed := time.Since(start)
		logger.Info("HTTP request handled",
			"method", r.Method,
			"path", path,
			"status", statusCode,
			"duration_ms", elapsed.Milliseconds(),
			"remote_addr", r.RemoteAddr,
		)
	}
}

func main() {
	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}

	logger.Info("Starting auto-remediation-service", "port", port)

	http.HandleFunc("/", httpLogger("/", "200", func(w http.ResponseWriter, r *http.Request) {
		recordMetrics("/", "200")
		fmt.Fprintf(w, "Failure Simulator Running\n")
	}))

	// 1. CPU Spike
	http.HandleFunc("/spike-cpu", httpLogger("/spike-cpu", "200", func(w http.ResponseWriter, r *http.Request) {
		recordMetrics("/spike-cpu", "200")
		durationSec := 300
		logger.Info("CPU spike triggered", "duration_seconds", durationSec, "remote_addr", r.RemoteAddr)
		fmt.Fprintf(w, "Triggering CPU Spike for %d seconds...\n", durationSec)

		go func() {
			logger.Info("CPU spike goroutine started", "duration_seconds", durationSec)
			end := time.Now().Add(time.Duration(durationSec) * time.Second)
			for time.Now().Before(end) {
				// busy loop — max CPU
				x := 1
				_ = x + 1
			}
			logger.Info("CPU spike goroutine finished")
		}()
	}))

	// 2. Memory Leak
	http.HandleFunc("/leak-memory", httpLogger("/leak-memory", "200", func(w http.ResponseWriter, r *http.Request) {
		recordMetrics("/leak-memory", "200")
		chunkMB := 50
		logger.Info("Memory leak triggered", "chunk_mb", chunkMB, "remote_addr", r.RemoteAddr)
		fmt.Fprintf(w, "Allocating %dMB of memory...\n", chunkMB)

		go func() {
			chunk := make([]byte, chunkMB*1024*1024)
			for i := range chunk {
				chunk[i] = 1
			}
			leakedMemory = append(leakedMemory, chunk)

			var m runtime.MemStats
			runtime.ReadMemStats(&m)
			logger.Info("Memory allocated", "total_allocated_mb", m.Alloc/1024/1024)
		}()
	}))

	// Helper to free memory
	http.HandleFunc("/free-memory", httpLogger("/free-memory", "200", func(w http.ResponseWriter, r *http.Request) {
		recordMetrics("/free-memory", "200")
		logger.Info("Freeing leaked memory via GC", "remote_addr", r.RemoteAddr)
		leakedMemory = nil
		runtime.GC()
		fmt.Fprintf(w, "Memory freed.\n")
	}))

	// 3. HTTP 500
	http.HandleFunc("/error-500", httpLogger("/error-500", "500", func(w http.ResponseWriter, r *http.Request) {
		recordMetrics("/error-500", "500")
		logger.Warn("Simulated 500 error triggered", "remote_addr", r.RemoteAddr)
		w.WriteHeader(http.StatusInternalServerError)
		fmt.Fprintf(w, "Internal Server Error\n")
	}))

	// Prometheus Metrics Endpoint
	http.Handle("/metrics", promhttp.Handler())

	logger.Info("Server is ready to accept connections", "addr", ":"+port)
	if err := http.ListenAndServe(":"+port, nil); err != nil {
		logger.Error("Server crashed", "err", err)
		os.Exit(1)
	}
}
