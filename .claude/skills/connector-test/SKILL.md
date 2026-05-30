---
name: connector-test
description: Validate a connector implementation — auth, sync, schema mapping, error handling, noise filtering
disable-model-invocation: true
---

Test the connector: $ARGUMENTS

1. Identify the connector in `services/connector-engine/connectors/`
2. Verify it implements all `BaseConnector` methods: `authenticate()`, `initial_sync()`, `continuous_sync()`, `disconnect()`, `health_check()`, `get_schema()`
3. Run the connector's test suite
4. Check:
   - Authentication works with configured credentials
   - Initial sync processes records correctly
   - Data written to correct Layer 1 table with org_id
   - Data dumped to correct `data/` folder (Phase 1-2)
   - Noise filtering applied correctly (check skip counts)
   - Sync status logged to `connector_sync_log`
   - Errors handled gracefully (one connector failure doesn't affect others)
   - Continuous sync detects new data
5. Report results:
   - Records processed / skipped / failed
   - Layer 1 row counts
   - File counts in data/ folder
   - Any errors or warnings
