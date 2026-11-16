import os

ORG_ID = "org_1"
SECRET_KEY = os.environ.get("SECRET_KEY", "salesflow-dev-secret-change-in-production")
ACCESS_TOKEN_MINUTES = 15
REFRESH_TOKEN_DAYS = 7
MONGO_URI = os.environ.get("MONGO_URI", "")  # e.g. mongodb://localhost:27017 (replica set required)
MONGO_DB = os.environ.get("MONGO_DB", "salesflow")
DEMO_REALTIME = os.environ.get("DEMO_REALTIME", "1") == "1"  # background live-event simulator

# Business rules catalog (section 6 of the PRD) - defaults, editable per org at runtime
RULES = {
    "BR-01": {"rule": "A customer plan lasts 30 days from its start date", "value": 30, "unit": "days", "type": "int"},
    "BR-02": {"rule": "Operating hours are split into one-hour slots", "value": "10:00-22:00", "unit": "", "type": "text"},
    "BR-06": {"rule": "Reroute threshold: utilisation at which new bookings move to the least busy store", "value": 90, "unit": "%", "type": "int"},
    "BR-07": {"rule": "Far-location price benefit above the distance threshold", "value": 10, "unit": "% above 5 km", "type": "int"},
    "BR-07KM": {"rule": "Far-location distance threshold", "value": 5, "unit": "km", "type": "int"},
    "BR-08": {"rule": "Waitlist offer hold time", "value": 30, "unit": "min", "type": "int"},
    "BR-09H": {"rule": "Extra-charge rule: each extra hour costs this share of base price", "value": 40, "unit": "% of base", "type": "int"},
    "BR-09S": {"rule": "Extra-charge rule: each extra employee costs this share of base price", "value": 30, "unit": "% of base", "type": "int"},
    "BR-10": {"rule": "Renewal reminders start before plan end; renewal slots held", "value": 25, "unit": "day, hold 7 days", "type": "int"},
    "BR-12": {"rule": "Promotional messages allowed only inside these quiet hours", "value": "09:00-21:00", "unit": "", "type": "text"},
    "BR-13": {"rule": "Maximum promotional touches per customer per week", "value": 3, "unit": "per week", "type": "int"},
    "BR-15": {"rule": "Lead score bands: hot / warm / cold thresholds", "value": "hot >= 80, warm 65-79, cold < 65", "unit": "", "type": "text"},
    "BR-16": {"rule": "Lead response SLA by source (minutes)", "value": {"instagram_ads": 15, "google_ads": 10, "website_form": 10, "referral": 5, "walk_in_call": 5, "justdial": 5, "others": 15}, "unit": "min", "type": "json"},
    "UTIL_TARGET": {"rule": "Target store utilisation", "value": 85, "unit": "%", "type": "int"},
    "BOOK_AHEAD_DAYS": {"rule": "Booking window (days ahead)", "value": 60, "unit": "days", "type": "int"},
}

STAGE_NEW = "new"
STAGES = ["new", "contacted", "interested", "offered", "booked", "lost"]
STAGE_LABELS = {"new": "New Lead", "contacted": "Contacted", "interested": "Interested",
                "offered": "Appt. Offered", "booked": "Booked", "lost": "Lost"}
SOURCES = ["instagram_ads", "google_ads", "website_form", "referral", "walk_in_call", "justdial", "others"]
SOURCE_LABELS = {"instagram_ads": "Instagram Ads", "google_ads": "Google Ads", "website_form": "Website / Form",
                 "referral": "Referral", "walk_in_call": "Walk-In / Call", "justdial": "Justdial", "others": "Others"}
LOST_REASONS = ["Price", "Distance", "No Response", "Chose Competitor", "Timing"]
SLOT_HOURS = list(range(10, 22))  # 12 one-hour slots, 10:00 to 22:00 (BR-02)
ROLES = ["admin", "manager", "sales", "staff", "marketing", "readonly"]

# RBAC: section -> allowed roles (PRD section 4/16; section 16 was truncated in the source PRD,
# so this matrix follows the persona table: Admin all, Manager own-store ops, Sales pipeline,
# Staff my-schedule, Marketing campaigns, Read-only view.)
PERMISSIONS = {
    "dashboard":  {"admin", "manager", "sales", "marketing", "readonly"},
    "leads":      {"admin", "manager", "sales", "marketing"},
    "customers":  {"admin", "manager", "sales", "marketing"},
    "marketing":  {"admin", "marketing"},
    "appointments": {"admin", "manager", "sales"},
    "team":       {"admin", "manager"},
    "products":   {"admin", "manager", "sales", "marketing"},
    "insights":   {"admin", "manager", "sales", "marketing"},
    "settings":   {"admin"},
    "audit":      {"admin"},
    "myschedule": {"staff", "admin", "manager"},
}
LEAD_WRITE_ROLES = {"admin", "manager", "sales"}
