# Unread Message Indicator

This repository implements a high-performance $O(1)$ unread message tracking system using FastAPI and Valkey (Redis). 

Traditional SQL approaches using `COUNT(DISTINCT sender_id)` degrade in performance as the dataset grows. This architecture replaces the database query with a Redis Set, relying on the mathematical properties of sets to natively deduplicate senders and calculate cardinality in constant time.

## System Architecture

* **Key Schema:** `unread:{recipient_id}`
* **Data Structure:** Redis Set
* **Ingestion:** `SADD` natively deduplicates multiple messages from the same sender.
* **Retrieval:** `SCARD` reads the set metadata to return the unique sender count in $O(1)$ time.
* **State Reset:** `DEL` instantly drops the set from memory when the user reads their inbox.

## Setup and Execution

1. **Start Infrastructure**:
   ```bash
   docker compose up -d
