"""In-app user guide (Help tab). Plain text for CTkTextbox."""

USER_GUIDE = """
ROOTRECORD BUSINESS MANAGER — HOW IT WORKS
==========================================

Your data lives in a single SQLite database (see the status bar at the bottom for the file path). Back up that file to preserve everything.


DASHBOARD
---------
• Shows “today” using your Business timezone (Program Settings / Business details) or Windows system time when set to “system”.
• “Total time worked” counts unique clock time: overlapping time entries are merged so the same minutes are not added twice.
• Recent activity lists your latest logged blocks with local timestamps.


TIME
----
• Categories separate planning from doing: Evaluation is for notes and triage; Development is for execution (fixes, delivery, hands-on work). Break is non-work time. Defaults also include common business buckets—Research, Marketing, Meetings, Travel, Sales, Support, Operations, and more—so reports can show where hours go.
• Log what you are doing with a description; pick category and optional project.
• Clock in / break / clock out follow a simple state machine so one block closes before the next starts.
• Prompts (Program Settings) can remind you to log on a timer; you can set timeout actions there.
• Timed check-in popups only run while actively working; they pause during breaks and when clocked out.


MONEY
-----
• Income and expenses are stored with amount, currency, and description.
• Lists show recent rows; summaries in Work Log / Reports use the same currency fields on each entry.


CLIENTS
-------
• Contacts for billing and scheduling: name, company, email, phone, address, tax id, notes.
• Save creates a new row when ID is empty; enter an ID and Load to edit; Archive hides a client from pickers (data kept).


INVOICES
--------
• New draft creates an invoice with a starter line. Set client, status, issued/due dates (local time), tax, and notes, then “Save header / tax”.
• Line items: one row per line, format:
    description | quantity | unit price
  Example:  Consulting | 4 | 150.00
• Totals: subtotal from lines + tax. Export PDF uses Business details (Business tab) as “From” and the client as “Bill to”.
• Invoice numbers must be unique per user; edit carefully to avoid duplicates.


SCHEDULE
--------
• Events use start (and optional end) in local wall time, interpreted with your Business timezone.
• Link optional client and project. Status: scheduled, done, or cancelled.
• The list shows the next 30 days from “now” (UTC internally).


WORK LOG & REPORTS
------------------
• Work Log uses your local calendar day converted to UTC for queries — entries align with the day you expect in your timezone.
• Report range uses the same idea for start/end dates.
• Task breakdown merges overlaps within each category; total time is unique clock coverage.
• Reports tab: search across time, income, and expenses by text.
• CSV/PDF exports use the current report range (refresh the report panel first if needed).


STOCK & SUPPLIES
----------------
• One page for everything moving in and out: finished products you sell, plus supplies and materials (office, production inputs, packaging, parts).
• Products: SKU, unit (ea, box, hr, …), quantity on hand, reorder level, optional cost/price per unit, notes—your sellable / shippable catalog.
• Supplies & materials: category (free-form—e.g. Office, Production / raw, Packaging), vendor, unit, quantities, reorder level, notes.
• “Apply Δ qty” on each section adds or subtracts on-hand (e.g. -1 for a sale, +10 for a receipt) without re-saving the whole form.
• Archive hides the row from the list but keeps data in the database.


BUSINESS
--------
• Legal/name/address/contact fields feed invoice PDFs and exports. Timezone drives all “local day” and schedule parsing.


SETTINGS
--------
• Theme, prompt timing, currency default, and other app preferences.
• Prompt timing applies to active work only (not while on break or clocked out).


ABOUT
-----
• Product information and links (website, terms, privacy).


TIPS
----
• If totals looked “too high” before, overlapping duplicate rows were the usual cause; the app now merges time for summaries. In Work Log, use Bulk Edit: tick entries in that dialog, set actions (delete, merge, shift time, category/project), then Apply Bulk Edit. Selection is only by those checkboxes, not the main table.
• For invoices, always click “Save line items” after editing the multiline editor.
• Restart the app after updates so database migrations can run.

"""
