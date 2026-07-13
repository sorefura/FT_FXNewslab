# Legacy Swap Bot Investigation Manifest

Inspected on 2026-07-13 at:

```text
C:\Users\soref\OneDrive\ドキュメント\VSCode\Test_app_trade-main
```

The inspected directory had no Git metadata. It remains an external baseline and is not copied
or modified by ExecPlan 0001.

## Safety behavior to preserve in new contracts

- Live submission requires both configuration enablement and `LIVE_TRADING_ARMED=YES`.
- GMO Private POST is not retried after a timeout or connection ambiguity.
- Low margin triggers the kill-switch behavior.
- Dry-run is the default.
- Existing position-count limits prevent an additional same-pair position.

## Evidence hashes

| Relative path | SHA-256 |
|---|---|
| `src/strategy.py` | `A8FF0F73D62FD3EF3F294CB79C15EE3A7A7C9B4F6DB0EEAB475468E1B7F04236` |
| `src/risk_manager.py` | `7D20F54CF12816C98FB79C54D3800C3FBB1121A91E24E732A72A41F3F3654EE7` |
| `src/execution.py` | `834AB9C93805AB966E59F979316CF67DBCB0C6DEDD84143D2EF8044833CFEE15` |
| `src/adapters/gmo_broker.py` | `36C2491531867A67E4CD6187F7790C5AD959CAB4846540B6029896BF2A1CDB05` |
| `config/settings.yaml` | `2A980A1553B97634F590984BD5428A3966A7616C246CD4A092D567D73AA1860F` |
| `config/system_prompt.txt` | `1A9FCC6836A7C3AB66E1CC49BBD0F682BFD33849327276D82127A839B4929093` |

No secret file was read or hashed.

