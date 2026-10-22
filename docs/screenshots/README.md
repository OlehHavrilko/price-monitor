# Screenshots

Images in this folder are taken from real runs; none are mock-ups.

The `web-*.png` files were captured in Chromium (1280 px, light theme) from
`docker compose --profile web up` on clean volumes. The demo catalog went through the `price_drop`,
`out_of_stock`, `product_missing` and `initial` scenarios, with tracking set on `00123`, `01001` and
`01002`. Collections ran every 15 seconds.

| File | Page |
| --- | --- |
| `web-sources.png` | Джерела — source cards |
| `web-products.png` | Товари — products of the last snapshot |
| `web-product-history.png` | Товар `00123` — price chart 24.90 → 19.90 → 24.90 and events |
| `web-events.png` | Події — price changes, sold out / back in stock, removed / returned |
| `web-report.png` | Звіт — period form |
| `web-status.png` | Стан — queue, database and recent collections |
| `web-collage.png` | all six dashboard pages in one picture (used on the freelance portfolio) |
