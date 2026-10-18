"""
Reset UbeKeso Cart to a brand-new, empty state.

    python reset_data.py          asks you to confirm first
    python reset_data.py --yes    no question asked

This deletes ALL accounts, orders, sales, loyalty cards, stock numbers and the activity log,
then creates a fresh owner (admin) account. Stop the server (Ctrl+C) before you run this.
Your pictures and promo video in the media folder are NOT touched.
"""
import os
import sys

import server


def main():
    if "--yes" not in sys.argv:
        print("This will erase ALL accounts, orders, loyalty cards, stock and logs.")
        if input("Type YES to continue: ").strip() != "YES":
            print("Cancelled. Nothing was changed.")
            return
    for path in (server.DATA_FILE, server.DATA_FILE + ".bad", server.DATA_FILE + ".tmp"):
        if os.path.exists(path):
            os.remove(path)
    server.load()          # creates a clean data.json with only the owner account
    admin = next(u for u in server.DB["users"].values() if u["role"] == "admin")
    print("Done. The database is brand new.")
    print("Owner login:  {}  /  {}".format(admin["email"], server.ADMIN_PASSWORD))
    print("Start the app with: python run.py")


if __name__ == "__main__":
    main()
