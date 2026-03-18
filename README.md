# Media Feed System: High-Throughput State Management

This repository contains a decoupled media upload pipeline and a real-time state management service. It is engineered to bypass standard relational database bottlenecks during high-frequency read/write operations and binary file storage.

## Architectural Architecture

This system eliminates two primary scaling failures found in naive backend designs:
1. **The Upload Bottleneck:** Routing binary file uploads through an API server blocks worker threads and consumes excessive bandwidth. Solved via S3 Presigned URLs.
2. **The Unread Indicator Bottleneck:** Executing relational `COUNT()` queries for unread messages on every client poll exhausts database CPU. Solved via in-memory Redis Set deduplication.

## Technology Stack

* **Compute:** FastAPI (Python)
* **Primary Database:** PostgreSQL
* **In-Memory Datastore & Queue:** Valkey (Redis)
* **Object Storage:** MinIO (S3 Compatible)
* **Containerization:** Docker

## Core System Flows

### 1. Decoupled Media Upload
* **URL Request:** Client requests an upload token. FastAPI returns a time-bound MinIO Presigned URL.
* **Direct Upload:** Client executes an HTTP PUT directly to the MinIO bucket. The API processes zero bytes of the media payload.
* **Async Processing:** Client confirms the upload. FastAPI commits metadata to PostgreSQL and pushes the event to a Valkey message queue for asynchronous hashtag extraction.

### 2. Real-Time State Management (Unread Indicators)
* **Ingestion (SADD):** When a message is sent, the Sender ID is pushed to a recipient-specific Valkey Set (`unread:{user_id}`). Valkey natively deduplicates the entries in memory.
* **Retrieval (SCARD):** The client polls for unread counts. The API returns the integer length of the Valkey Set in O(1) time, completely bypassing PostgreSQL.
* **State Reset (DEL):** When the user opens their inbox, the API deletes the Valkey Set, instantly resetting the unread state.

## Local Execution

1. Boot the infrastructure (PostgreSQL, Valkey, MinIO):
```bash
docker-compose up -d
