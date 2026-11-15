import os
import json
import hashlib
from pathlib import Path
import multiprocessing
from kb_manager import KnowledgeBase

DOCS_DIR = "./knowledge_docs"
HASH_RECORD_FILE = Path(DOCS_DIR) / ".ingested_hashes.json"
BATCH_SIZE = 20  # Number of text chunks to send to ChromaDB in a single batch


def compute_file_hash(file_path: Path) -> str:
    """Computes SHA-256 hash of a file for change tracking."""
    hasher = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            while chunk := f.read(8192):
                hasher.update(chunk)
        return hasher.hexdigest()
    except Exception as e:
        print(f"[!] Error computing hash for {file_path.name}: {e}")
        return ""


def load_hash_records() -> dict:
    """Loads previously ingested file hashes from JSON."""
    if HASH_RECORD_FILE.exists():
        try:
            with open(HASH_RECORD_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[!] Error loading hash records: {e}")
    return {}


def save_hash_records(records: dict):
    """Saves updated file hashes to JSON."""
    try:
        with open(HASH_RECORD_FILE, "w", encoding="utf-8") as f:
            json.dump(records, f, indent=4)
    except Exception as e:
        print(f"[!] Error saving hash records: {e}")


def chunk_text(text: str, chunk_size: int = 600, overlap: int = 100) -> list[str]:
    """Splits large text documents into smaller overlapping chunks."""
    chunks = []
    start = 0
    try:
        while start < len(text):
            end = start + chunk_size
            chunks.append(text[start:end])
            start += chunk_size - overlap
    except Exception as e:
        print(f"[!] Error while chunking text: {e}")
    return chunks


def process_single_file(args):
    """
    Worker function executed in parallel.
    Reads file, chunks text, creates batches, and pushes results to queue.
    """
    file_path_str, file_hash, result_queue = args
    file_path = Path(file_path_str)
    pid = os.getpid()
    print(f"[*] [Worker PID: {pid}] Processing: {file_path.name}")

    try:
        kb = KnowledgeBase()

        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        chunks = chunk_text(content, chunk_size=600, overlap=100)
        if not chunks:
            result_queue.put(("skipped", file_path_str, file_hash, f"Skipped (empty): {file_path.name}"))
            return

        # Prepare batch payloads for vectorization
        ids_batch, docs_batch, metadatas_batch = [], [], []

        for i, chunk in enumerate(chunks):
            doc_id = f"{file_path.stem}_chunk_{i}"
            metadata = {"source": file_path.name, "chunk_index": i}

            ids_batch.append(doc_id)
            docs_batch.append(chunk)
            metadatas_batch.append(metadata)

            # Send batch when it hits BATCH_SIZE or reaches final chunk
            if len(ids_batch) >= BATCH_SIZE or i == len(chunks) - 1:
                try:
                    # If KnowledgeBase has a batch method, or loop through items safely:
                    for b_id, b_doc, b_meta in zip(ids_batch, docs_batch, metadatas_batch):
                        kb.add_document(doc_id=b_id, text=b_doc, metadata=b_meta)
                except Exception as batch_err:
                    print(f"[!] Error uploading batch for {file_path.name}: {batch_err}")

                # Reset batches
                ids_batch, docs_batch, metadatas_batch = [], [], []

        print(f"[+] Successfully trained file: {file_path.name} ({len(chunks)} chunks)")
        result_queue.put(("success", file_path_str, file_hash, f"Trained: {file_path.name}"))

    except UnicodeDecodeError:
        err_msg = f"Failed (Encoding UTF-8 error): {file_path.name}"
        print(f"[!] {err_msg}")
        result_queue.put(("failed", file_path_str, file_hash, err_msg))
    except Exception as e:
        err_msg = f"Failed ({str(e)}): {file_path.name}"
        print(f"[!] {err_msg}")
        result_queue.put(("failed", file_path_str, file_hash, err_msg))


def ingest_documents():
    path = Path(DOCS_DIR)

    try:
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
            print(f"[!] Created directory '{DOCS_DIR}'. Drop .txt or .md files inside and re-run.")
            return

        files = list(path.glob("**/*.txt")) + list(path.glob("**/*.md"))
        if not files:
            print(f"[!] No text or markdown files found in '{DOCS_DIR}'.")
            return

        hash_records = load_hash_records()
        files_to_process = []
        skipped_files = []

        for f in files:
            if f.name.startswith("."):
                continue
            file_hash = compute_file_hash(f)
            if not file_hash:
                continue

            str_path = str(f)
            if str_path in hash_records and hash_records[str_path] == file_hash:
                skipped_files.append(f.name)
            else:
                files_to_process.append((str_path, file_hash))

        if not files_to_process:
            print(f"[+] All {len(files)} files are already up-to-date in the Knowledge Base!")
            return

        # Resource management & Worker limit
        total_cpus = multiprocessing.cpu_count()
        safe_workers = max(1, total_cpus - 1)
        print(f"[*] Detected {total_cpus} CPU cores. Using {safe_workers} parallel workers for ingestion.")

        # Setup Multiprocessing Manager Queue for results subscription
        manager = multiprocessing.Manager()
        result_queue = manager.Queue()

        # Pack tasks with queue reference
        task_payloads = [(f_path, f_hash, result_queue) for f_path, f_hash in files_to_process]

        multiprocessing.freeze_support()
        with multiprocessing.Pool(processes=safe_workers) as pool:
            # Async apply tasks
            pool.map_async(process_single_file, task_payloads)

            # Close pool and wait for completion
            pool.close()
            pool.join()

        # Collect all results from queue
        results = []
        while not result_queue.empty():
            results.append(result_queue.get())

        # Update persistent hash storage for successfully trained files
        trained_count = 0
        failed_count = 0
        trained_details = []
        failed_details = []

        for status, f_path, f_hash, msg in results:
            if status == "success":
                hash_records[f_path] = f_hash
                trained_count += 1
                trained_details.append(msg)
            else:
                failed_count += 1
                failed_details.append(msg)

        save_hash_records(hash_records)

        # Final Training Summary Report
        print("\n" + "=" * 35 + " KNOWLEDGE BASE TRAINING SUMMARY " + "=" * 35)
        print(f"Total Files Found      : {len(files)}")
        print(f"Successfully Trained   : {trained_count}")
        print(f"Skipped (Unchanged)    : {len(skipped_files)}")
        print(f"Failed / Errors        : {failed_count}")
        print("-" * 103)

        if trained_details:
            print("[+] Successfully Trained Files:")
            for item in trained_details:
                print(f"    - {item}")

        if skipped_files:
            print("\n[*] Skipped Files (No changes detected):")
            for name in skipped_files:
                print(f"    - Skipped: {name}")

        if failed_details:
            print("\n[!] Failed Files:")
            for item in failed_details:
                print(f"    - {item}")

        print("=" * 103)
        print("[+] Ingestion & training pipeline completed successfully!")

    except Exception as e:
        print(f"[!] Critical error during ingestion: {e}")


if __name__ == "__main__":
    ingest_documents()