#!/usr/bin/env python3

from __future__ import annotations

from _api_common import choose_base, post_json, print_json


def main() -> None:
    base = choose_base()
    payload = {
        "jobs": [
            {
                "target_name": "OGA",
                "search_query": "O-GlcNAcase 9BA9 6PM9 5UN9 5M7T",
                "fasta_seq": ">EXAMPLE_FASTA\nMKT...",
            },
            {
                "target_name": "DYRK1A",
                "search_query": "DYRK1A 3ANR 4MQ2 6EIF 7AKL",
                "fasta_seq": ">EXAMPLE_FASTA\nMKT...",
            },
        ],
        "delay_seconds": 2,
    }

    print(f"Using base: {base}")
    batch = post_json(f"{base}/api/batches", payload)
    print_json(batch)


if __name__ == "__main__":
    main()
