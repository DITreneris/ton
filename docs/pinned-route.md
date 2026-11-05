# Pinned route

Identity is addresses, never tickers.

| Item | Address |
| --- | --- |
| Network | `mainnet` |
| USD₮ Jetton master | `EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs` |
| Native GRAM | `EQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAM9c` |
| STON.fi v2 pool | `EQCGScrZe1xbyWqWDvdI6mzP-GAcAWFv6ZXuaJOuSqemxku4` |
| STON.fi v2 router | `EQCS4UEa5UaJLzOyyKieqQOQ2P9M-7kXpkO5HnP3Bv250cN3` |

Scope verification fetches STON.fi asset, pool, and router metadata, then both swap simulations with `pool_address` pinned. Any look-alike ticker, wrong network, wrong master, or a quote that lands on another router or pool fails closed and latches halt.
