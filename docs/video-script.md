# Demo video script (about 75 seconds)

Goal: show one thing from start to finish. A shop changes, the service notices, the owner gets a
message and a report. Record the screen with Win+Alt+R (Xbox Game Bar), OBS Studio or any recorder.

## Before recording

1. Open the bot chat in Telegram Desktop on the left half of the screen.
2. Open a terminal on the right half and connect to the server: `ssh oracle`, then
   `cd ~/apps/price-monitor`.
3. Open `http://127.0.0.1:8080` in a browser (through `ssh -L 8080:127.0.0.1:8080 oracle`) to show the
   demo shop. Put it behind the terminal or on a second desktop for the first shots.
4. Make sure the chat is not cluttered: clear the history in the chat if old test messages are there.

## Shots

| Time | What is on the screen | What to say (or write as a caption) |
| --- | --- | --- |
| 0-8 s | The bot page: description and the Start button, then `/start` | "Price Monitor watches product prices and stock in online shops and writes to Telegram when something changes." |
| 8-18 s | Buttons: Shops, a shop, Products, a product card, press "Track" | "I choose products to follow. The service checks the shop on a schedule." |
| 18-25 s | The demo shop in the browser, then the terminal | "Real shops do not change on demand, so for the demo there is a test shop whose prices a script changes." |
| 25-55 s | Terminal: `docker compose exec -T monitor price-monitor demo-play --pause 6`; in the chat the explanation messages and alerts arrive one after another | "Prices drop, an item sells out and comes back, a product disappears, the shop breaks. Each message says what happened, why I got it and how to turn it off." |
| 55-65 s | Chat: the message "shop is unavailable", then "works again" | "When the page layout changes the service does not invent changes, it reports the problem once." |
| 65-75 s | Bot button "XLSX report" and the file opened in Excel (current state and history sheets) | "A report with the current state and the history of changes." |

## Screenshots for the project card (3 to 5)

1. The demo shop before and after (`docs/screenshots/shop-initial.png`, `shop-price-drop.png` already exist).
2. A price-drop alert with the picture card in the Telegram chat.
3. The button menu with a product card in the chat.
4. The XLSX report open in Excel.

Use only real screenshots of the running application. Do not mock them up.
