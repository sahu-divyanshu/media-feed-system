# Media Feed System

This repository implements a decoupled media upload and asynchronous processing pipeline. It demonstrates high-performance system design patterns by offloading binary storage to an external object store and delegating heavy compute tasks to background workers.

## Architecture

The system consists of the following isolated components:

1. **Client**: Requests upload authorization, uploads binaries directly to storage, and notifies the backend upon completion.
2. **FastAPI Backend**: Handles authorization, metadata persistence, and job enqueuing. It does not process or route binary files.
3. **MinIO (S3 Compatible)**: Handles direct binary ingestion via cryptographically signed URLs.
4. **PostgreSQL**: Stores relational metadata for user posts.
5. **Valkey (Redis)**: Acts as the message broker for background jobs and the high-speed data store for O(1) deduplication algorithms.
6. **Python Worker**: A standalone process that consumes the Valkey queue, extracts hashtag metadata via regex, and updates trending metrics.

## System Flow

1. **Upload Authorization**: The client requests a Presigned URL from the FastAPI server.
2. **Direct Ingestion**: The client executes an HTTP PUT directly to MinIO using the Presigned URL. The FastAPI server remains idle.
3. **Metadata Commit**: The client notifies the FastAPI server. The server writes post metadata to PostgreSQL.
4. **Asynchronous Handover**: The server pushes the job payload to a Valkey list and immediately returns a 202 Accepted response to the client.
5. **Background Processing**: The worker process blocks on the Valkey queue (BLPOP). Upon receiving a payload, it extracts hashtags and updates a Valkey Sorted Set using pipelined ZINCRBY commands.
6. **O(1) Unread Indicator**: Unread messages are tracked using Redis Sets (SADD). This provides native deduplication. Retrieving the unread count uses SCARD, an O(1) metadata read.

## Prerequisites

* Docker and Docker Compose
* Python 3.13

## Setup and Execution

1. **Start Infrastructure**:
   ```bash
   docker compose up -d
