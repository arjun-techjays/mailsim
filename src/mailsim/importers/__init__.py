"""Corpus importers: turn public email corpora into pseudonymized, scenario-ready material.

`enron` reads the LLM-PBE/enron-email parquet shards (bodies only), cleans and pseudonymizes
them into a fictional company (`pseudonymize`), classifies topics (`classify`) and `leadership`
generates one scenario per leadership inbox from the result. `pii` scans any output for
residual real identifiers. Nothing here opens a network connection: the shards are downloaded
beforehand (docs/leadership-inboxes.md).
"""
