# UbeKeso Cart

A phone-style web app for an ube cheese turon cart in Cebu City, now with **customer accounts, a staff role, an owner (admin) account and live order updates**.

Built for the **Event-Driven Programming** final project. The app logic is Python (**PyScript** in the browser, plus a small standard-library Python server). There is no JavaScript logic.

## How to run

1. Install Python 3 (python.org).
2. Open a terminal in this folder and run:

   ```
   python run.py
   ```

   (Some computers use `python3 run.py` or `py run.py`.)
3. The browser opens the app.

To try it on phones, run `python run.py --lan`, then open the address it prints (for example `http://192.168.1.20:8000/index.html`) on a phone on the same Wi-Fi. Orders placed on the phone appear on the other devices within about 3 seconds.

Notes
- Internet is needed for PyScript and Google Fonts. Do not double-click `index.html`; use `run.py`.
- The app now needs `run.py` (the server stores accounts and orders). It will not work on a static host such as GitHub Pages.

## Start fresh (reset the database)

The `data.json` in this folder is already brand new: one owner account, no customers, no staff, no orders, no sales, no loyalty cards, every ingredient at 0 and an empty activity log.

To wipe everything again at any time (for example before your presentation), stop the server with Ctrl+C and run:

```
python reset_data.py
```

Type `YES` when asked (or use `python reset_data.py --yes`). Your logo and promo video in `media/` are not touched.

## Logo, About us & FAQs, and the promo video

- **Logo:** your Ube-Kesoturon picture is used on the loading screen, sign in, create account, the About page, and as the browser icon. The files are in `media/` (`logo.jpg`, `logo-small.jpg`, `logo-icon.png`). Replace them with another picture of the same names to change the logo.
- **About us & FAQs:** a new page anyone can open, even before signing in. Find it with **About us & FAQs** on the sign in screen, on the customer **Account** tab, and at the bottom of the staff/owner **Home**. The questions are written in `faq_items()` in `main.py`; prices and the number of stamps update by themselves from `server.py`.
- **Promo video space:** a "Watch: Ube-Kesoturon" card on the staff/owner **Home**, on the customer **Order** screen and on the About page. It shows a "Promo video coming soon" box until you add a video. To add yours, save it as `media/promo.mp4` (`promo.webm` or `promo.mov` also work) and refresh the page. No restart is needed. Tip: keep it short and compressed (under about 30 MB) so it loads quickly on phones.

## Accounts

| Who | How they get an account | What they see |
| --- | --- | --- |
| **Customer** | "Create a customer account" on the sign in screen: name, email **or** mobile number, password (8+ characters) | Order ahead, My orders (live status), My card (loyalty stamps), Account |
| **Owner (admin)** | Created automatically the first time the server starts | Everything: Home dashboard with "Needs attention", all orders, Staff accounts and activity log, Stock, Loyalty, Reports |
| **Staff** | Created by the owner on the Staff tab (the + button) | Home, Sell, Orders, Loyalty, Stock (no Reports, no staff list) |

**Owner login (first run):** `admin@ubekeso.ph` / `Admin#2026`. The app shows a "Change your password" banner until you change it. To use your own from the start, set `UBEKESO_ADMIN_EMAIL` and `UBEKESO_ADMIN_PASSWORD` before the first run.

Sign in with email or mobile number. Mobile numbers are accepted as `09171234567`, `+63 917 123 4567` and similar.

## The live order flow

1. A customer places an order (pieces, extra cheese, pickup time, cash or e-wallet at pickup).
2. Staff and the owner see it in **Orders** with a red badge on the tab and a "New order from ..." message.
3. Staff tap **Start frying**, **Mark ready**, then **Picked up and paid**. The customer's **My orders** screen follows each step and shows a message when it is ready.
4. The owner sees every order, who started it, and the activity log of what each staff member did and when (the owner can delete single entries or clear the log). The Staff tab also shows who is online right now.
5. When an order is picked up, it is added to today's sales and the customer gets a loyalty stamp.

Customers can cancel an order while it is still **New**.

## Forgot password

On the sign in screen tap **Forgot password?**, enter the account's email or mobile number, then enter the 6-digit code and a new password. Codes work for 10 minutes, once, and after a reset all old sign-ins are signed out.

This project cannot send real SMS or email, so it runs in **demo mode**: the code is shown in the app and printed in the server terminal. To send real messages, connect a service inside `send_reset_code()` in `server.py` and start the server with `UBEKESO_DEMO_CODES=0` so the code is no longer shown in the app.

## Files

| File | What it does |
| --- | --- |
| `index.html` | Page shell and PyScript loader |
| `style.css`, `extra.css` | Styles |
| `main.py` | The app in the browser: state, screens, event handlers, live polling |
| `server.py` | Backend: accounts, roles, orders, activity log, password reset, saving to `data.json` |
| `run.py` | Starts the server and opens the browser |
| `reset_data.py` | Erases all data and starts the database fresh |
| `media/` | Logo pictures and your promo video (`promo.mp4`) |
| `pyscript.toml` | PyScript config |
| `data.json` | Created on first run; all accounts, orders and stock. Delete it to start fresh |

## Event-Driven Programming in `main.py`

| Event | Where | Handler |
| --- | --- | --- |
| `click` (event delegation on `data-action`) | all buttons, tabs, steppers, chips | `on_click` -> `CLICK_ACTIONS` |
| `submit` | sign in, sign up, forgot and reset password, change password, new card, pre-order, stock, new staff | `on_submit` -> `SUBMIT_HANDLERS` |
| `input` | Loyalty search box | `on_input` |
| `change` | Extra cheese switch (sale and customer order), pickup time | `on_change` |
| `keydown` | Escape closes a form sheet | `on_keydown` |
| `setInterval` timer, every 3 s | live updates: asks the server what changed, redraws when something did | `on_tick` -> `poll` |
| `visibilitychange` | refreshes at once when you return to the tab | `on_visible` |
| `setTimeout` timer | toast message hides itself | `toast` |

The screen is never redrawn while someone is typing in a field or has a form open; the update is applied right after.

## Security notes

- Passwords are stored only as salted PBKDF2-SHA256 hashes. Sign in is rate limited (5 wrong tries = 1 minute wait).
- Every action is checked on the server by role. A customer cannot create staff or move orders, even by editing the page.
- `server.py`, `run.py`, `reset_data.py` and `data.json` are never served to browsers. Only pictures and videos inside `media/` are shared.
- Running over plain HTTP on your own Wi-Fi is fine for a school project. For a public deployment use HTTPS and a real database.

## Things you can change

At the top of `server.py` (`CONFIG`): `price`, `extra_cheese_price` (5 is a placeholder; set your real price), `daily_target`, `month_goal`, `cost_per_piece`, stamps for a free piece, low-stock levels and minimum password length. The app picks them up automatically.

Behavior choices where the wireframes were silent
- Starting to fry an order subtracts those pieces from stock; "Picked up and paid" adds the order to today's sales.
- One stamp is added per sale or per picked-up order.
- Today's count resets at midnight and is saved to the week's history. Sales and the Reports numbers start at zero.
- Everything starts empty: no orders, no sales, every ingredient at 0 (use Add delivery to enter stock) and no sample loyalty cards.
