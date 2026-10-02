# GENERATED from 00_config.ipynb -- keep in sync (re-export the code cells when the notebook changes).
# Databricks runs `%run ./00_config` against the notebook; locally IPython's `%run ./00_config` falls back to this file.

# Cell 1 — Names, sizes, seeds
catalog = "bootcamp_students"
schema  = "maintops"
volume  = "maintops_docs"

VOLUME_ROOT = f"/Volumes/{catalog}/{schema}/{volume}"
CV_DIR      = f"{VOLUME_ROOT}/handyman_cvs"

def fq(table): return f"{catalog}.{schema}.{table}"

T_ADDRESS_POOL   = fq("synth_address_pool")     # real German addresses (OSM), split into user / handyman purpose
T_USERS          = fq("synth_users")            # same columns as maintops.users; clients AND handymen (is_handyman flag)
T_HANDYMAN_DETAILS = fq("synth_handyman_details")  # same columns as maintops.handyman_details (user_id -> users.id)
T_HANDYMEN_TRUTH = fq("synth_handymen_truth")   # generator ground truth (quality, CV structure) — NOT part of the app schema
T_INCIDENTS      = fq("synth_incidents")        # same columns as maintops.incidents (1M+ rows, Delta)

# users.id layout: 1..N_USERS are clients, N_USERS+1..N_USERS+N_HANDYMEN are handymen (users with is_handyman = true)
N_USERS     = 100_000     # clients
N_HANDYMEN  = 10_000
N_INCIDENTS = 1_000_000

SEED = 42

# Synthetic login emails: realistic, name-based, lowercase, unique across users AND handymen (login is by email).
# Only RFC 2606 reserved domains are used, so no mail can ever reach a real person.
EMAIL_DOMAINS = ["example.com", "example.com", "example.com", "example.org", "example.net"]
_EMAIL_PATTERNS = ["{f}.{l}", "{f}.{l}", "{f}{l}", "{f}_{l}", "{fi}.{l}", "{l}.{f}", "{f}-{l}"]

def make_email(rng, first, last, used):
    """Return a new unique email built from the person's name and add it to `used` (a set of lowercase emails).
    Names are ASCII in this project; anything else is stripped so the address stays valid."""
    import re
    f, l = (re.sub(r"[^a-z]", "", x.lower()) for x in (first, last))
    for attempt in range(50):
        local = str(rng.choice(_EMAIL_PATTERNS)).format(f=f, l=l, fi=f[:1])
        if attempt > 0 or rng.random() < .45:             # many people get a number, everyone does after a collision
            local += str(rng.integers(1, 100 if attempt < 5 else 100_000))
        email = f"{local}@{rng.choice(EMAIL_DOMAINS)}"
        if email not in used and len(email) <= 64:
            used.add(email)
            return email
    raise RuntimeError(f"Could not make a unique email for {first} {last}")

# One demo password for every synthetic account (hashed with Argon2id in notebooks 02/03)
DEMO_PASSWORD = "MaintOps!2026"

# Accounts are created before the incident history starts, so every incident happens after both parties exist
ACCOUNTS_START = "2020-07-01 00:00:00"
ACCOUNTS_END   = "2020-12-31 00:00:00"
CLIENT_ACTIVE_SHARE = 0.97   # share of clients with is_active = true (handymen are all active)

# Incident timeline. Historical incidents end 2026-09-14; the few non-finished ones sit in the last days.
HISTORY_START = "2021-01-01 00:00:00"
HISTORY_END   = "2026-09-14 00:00:00"
ACTIVE_START  = "2026-09-17 00:00:00"
ACTIVE_END    = "2026-09-19 00:00:00"

# Cell 2 — Regions: real addresses come from OpenStreetMap extracts (Geofabrik). Add/remove regions freely;
# weights say what share of users / handymen live there (must sum to 1).
REGIONS = {
    "Berlin":   {"url": "https://download.geofabrik.de/europe/germany/berlin-latest.osm.pbf",   "weight": 0.40},
    "Hamburg":  {"url": "https://download.geofabrik.de/europe/germany/hamburg-latest.osm.pbf",  "weight": 0.30},
    "Bremen":   {"url": "https://download.geofabrik.de/europe/germany/bremen-latest.osm.pbf",   "weight": 0.10},
    "Saarland": {"url": "https://download.geofabrik.de/europe/germany/saarland-latest.osm.pbf", "weight": 0.20},
}
assert abs(sum(r["weight"] for r in REGIONS.values()) - 1) < 1e-9

def allocate(total):
    """Split `total` across regions by weight (largest-remainder), returns {region: n}."""
    raw   = {k: total * v["weight"] for k, v in REGIONS.items()}
    alloc = {k: int(x) for k, x in raw.items()}
    left  = total - sum(alloc.values())
    for k in sorted(raw, key=lambda k: raw[k] - alloc[k], reverse=True)[:left]:
        alloc[k] += 1
    return alloc

# Cell 3 — Controlled taxonomy (matches README.md, 'Specialisations, skills and experience')
SPECIALISATIONS = ["plumbing", "electrical", "heating_hvac", "carpentry", "painting",
                   "roofing", "flooring", "appliance_repair", "locksmith", "general_maintenance"]

# Share of handymen whose PRIMARY specialisation this is — and share of incidents of this type
SPEC_WEIGHTS = {"plumbing": .17, "electrical": .15, "heating_hvac": .10, "carpentry": .09, "painting": .10,
                "roofing": .05, "flooring": .07, "appliance_repair": .12, "locksmith": .06, "general_maintenance": .09}

# Trades that often go together (used to add a 2nd / 3rd specialisation)
RELATED = {
    "plumbing": ["heating_hvac", "general_maintenance"], "heating_hvac": ["plumbing", "electrical"],
    "electrical": ["appliance_repair", "heating_hvac"],  "appliance_repair": ["electrical", "general_maintenance"],
    "carpentry": ["flooring", "roofing", "locksmith"],   "flooring": ["carpentry", "painting"],
    "painting": ["flooring", "general_maintenance"],     "roofing": ["carpentry"],
    "locksmith": ["carpentry", "general_maintenance"],   "general_maintenance": ["painting", "plumbing", "carpentry"],
}

SKILLS = {
    "plumbing": ["pipe repair", "leak detection", "toilet installation", "tap replacement", "drain unblocking",
                 "water heater installation", "shower installation", "pipe soldering", "sink installation", "water pressure diagnosis"],
    "electrical": ["socket installation", "fuse box upgrade", "lighting installation", "fault finding", "cable laying",
                   "smoke detector installation", "EV charger installation", "circuit testing", "switch replacement", "intercom wiring"],
    "heating_hvac": ["radiator repair", "boiler maintenance", "thermostat installation", "underfloor heating", "heat pump servicing",
                     "air conditioning installation", "radiator bleeding", "ventilation cleaning", "flue inspection", "heating system flushing"],
    "carpentry": ["door fitting", "window repair", "furniture assembly", "shelf installation", "staircase repair",
                  "wardrobe building", "timber framing", "skirting boards", "kitchen worktop fitting", "wooden floor repair"],
    "painting": ["interior painting", "exterior painting", "wallpapering", "plaster repair", "mould treatment",
                 "ceiling painting", "wood varnishing", "filler and sanding", "facade painting", "radiator painting"],
    "roofing": ["roof tile repair", "gutter cleaning", "flat roof sealing", "skylight installation", "roof leak repair",
                "chimney flashing", "roof insulation", "gutter installation", "storm damage repair", "ridge tile fixing"],
    "flooring": ["laminate installation", "parquet sanding", "tile laying", "carpet fitting", "vinyl flooring",
                 "screed levelling", "grout repair", "threshold fitting", "floor insulation", "skirting installation"],
    "appliance_repair": ["washing machine repair", "dishwasher repair", "fridge repair", "oven repair", "dryer repair",
                         "hob repair", "extractor hood installation", "appliance installation", "coffee machine descaling", "freezer repair"],
    "locksmith": ["lock opening", "lock replacement", "cylinder change", "door security upgrade", "key cutting",
                  "burglary damage repair", "smart lock installation", "window lock fitting", "safe opening", "letterbox lock repair"],
    "general_maintenance": ["minor repairs", "curtain rail fitting", "TV wall mounting", "silicone resealing", "flat-pack assembly",
                            "garden fence repair", "picture hanging", "caulking", "door hinge adjustment", "property inspection"],
}

# Base hourly rate in EUR, gross (drives handyman_details.avg_price, an average HOURLY rate, never a job price).
# The generator adds quality / experience premiums: the median handyman charges ~1.28 x base, which gives realistic
# German rates, e.g. plumbing ~EUR 68/h, locksmith ~EUR 75/h, painting ~EUR 50/h, general maintenance ~EUR 42/h.
BASE_HOURLY_RATE = {"plumbing": 53, "electrical": 51, "heating_hvac": 56, "carpentry": 47, "painting": 39,
                    "roofing": 48, "flooring": 41, "appliance_repair": 48, "locksmith": 59, "general_maintenance": 33}

# Typical hours on site per job type. A completed job's hours_worked = typical x 0.5-1.5 (quarter hours, min 0.5) and
# amount_paid_eur = hours x the handyman's hourly rate (avg_price) x 0.9-1.1. The pipeline derives each handyman's
# hourly rate back from these figures, so it lands within a few cents of avg_price.
TYPICAL_JOB_HOURS = {"plumbing": 2.5, "electrical": 3.0, "heating_hvac": 3.5, "carpentry": 4.0, "painting": 7.0,
                     "roofing": 6.0, "flooring": 7.0, "appliance_repair": 1.5, "locksmith": 1.0, "general_maintenance": 2.0}

def billing_columns(incidents, handyman_details):
    """incidents (id, incident_type, status, handyman_user_id) + hours_worked, amount_paid_eur for completed jobs.
    Both figures come from a hash of the incident id, not rand(): the generator (05) and the backfill of existing
    incidents (08) produce exactly the same values."""
    from pyspark.sql import functions as F
    u = lambda salt: (F.abs(F.xxhash64(F.col("id"), F.lit(salt))) % 10000) / 10000.0
    typical = F.element_at(F.create_map(*[F.lit(x) for kv in TYPICAL_JOB_HOURS.items() for x in kv]), F.col("incident_type"))
    hours = F.greatest(F.lit(0.5), F.round(typical * (0.5 + u("hours")) * 4) / 4)
    rate = handyman_details.select(F.col("user_id").alias("handyman_user_id"), F.col("avg_price").cast("double").alias("_rate"))
    completed = (F.col("status") == "completed") & F.col("handyman_user_id").isNotNull() & F.col("_rate").isNotNull()
    return (incidents.join(F.broadcast(rate), "handyman_user_id", "left")
            .withColumn("hours_worked", F.when(completed, hours).cast("decimal(5,2)"))
            .withColumn("amount_paid_eur", F.when(completed, F.round(F.col("hours_worked") * F.col("_rate") * (0.9 + 0.2 * u("amount")), 2))
                                            .cast("decimal(10,2)"))
            .drop("_rate"))

# Cell 4 — People: German first / last names (ASCII-only spelling so PDFs render with any base font)
FIRST_NAMES = """Anna Maria Sophie Laura Julia Lena Lea Hannah Emma Mia Lisa Katharina Sarah Jana Nina Franziska Claudia Sabine Petra Monika
Andrea Susanne Birgit Karin Heike Ute Ingrid Christina Nadine Melanie Aylin Fatma Elif Zeynep Agnieszka Olga Natalia Ewa Ilona Yasmin Leyla
Lukas Leon Finn Jonas Paul Felix Maximilian Elias Noah Ben Tim Jan Niklas Tobias Daniel Michael Thomas Andreas Stefan Markus Christian
Alexander Sebastian Matthias Frank Jens Uwe Dieter Peter Klaus Jurgen Bernd Holger Ralf Oliver Marco Patrick Kevin Dennis Mehmet Ahmet Emre
Murat Can Burak Piotr Tomasz Marek Jakub Dmitri Sergej Ivan Andrei Nikolai Milan Goran Luca Mario Giovanni Karim Omar Yusuf Ali Hakan""".split()

LAST_NAMES = """Muller Schmidt Schneider Fischer Weber Meyer Wagner Becker Schulz Hoffmann Schafer Koch Bauer Richter Klein Wolf Schroder Neumann
Schwarz Zimmermann Braun Kruger Hofmann Hartmann Lange Schmitt Werner Schmitz Krause Meier Lehmann Schmid Schulze Maier Kohler Herrmann
Konig Walter Mayer Huber Kaiser Fuchs Peters Lang Scholz Moller Weiss Jung Hahn Schubert Vogel Friedrich Keller Gunther Frank Berger Winkler
Roth Beck Lorenz Baumann Franke Albrecht Schuster Simon Ludwig Bohm Winter Kraus Martin Schumacher Kramer Vogt Stein Jager Otto Sommer
Gross Seidel Heinrich Brandt Haas Schreiber Graf Schulte Dietrich Ziegler Kuhn Kuhne Pohl Engel Horn Busch Bergmann Thomas Voigt Sauer
Arnold Wolff Pfeiffer Yilmaz Kaya Demir Sahin Celik Aydin Ozturk Arslan Kowalski Nowak Wisniewski Wojcik Kaminski Lewandowski Petrov
Ivanov Popov Novak Horvat Kovac Rossi Russo Ferrari Esposito Hassan Ibrahim Nguyen""".split()

def make_phone(rng):
    return f"+49 1{rng.choice(['51','52','57','60','62','70','75','76','79'])} {rng.integers(1_000_000, 9_999_999)}"

# Cell 5 — Incident text material. Each problem is (text, base urgency 0..3): low, medium, high, critical
URGENCY_LEVELS = ["low", "medium", "high", "critical"]

PROBLEMS = {
 "plumbing": [
  ("The kitchen tap is dripping constantly", 0), ("Water is leaking from a pipe under my kitchen sink", 2),
  ("The toilet keeps running and will not stop", 1), ("The shower drain is completely blocked", 1),
  ("A pipe has burst in the bathroom and water is spreading across the floor", 3), ("There is no hot water anywhere in the flat", 2),
  ("The water pressure in the bathroom has dropped a lot", 1), ("I need a new washbasin installed in the guest toilet", 0),
  ("The washing machine hose is leaking behind the machine", 2), ("Water is coming through the ceiling from the flat upstairs", 3)],
 "electrical": [
  ("Half of the sockets in the living room have stopped working", 2), ("The fuse trips every time I switch on the oven", 2),
  ("I would like three new ceiling lights installed in the hallway", 0), ("There is a burning smell coming from a socket", 3),
  ("The light switch in the bedroom sparks when I use it", 3), ("I need a wallbox for my electric car installed in the garage", 0),
  ("The doorbell and intercom have both stopped working", 1), ("Smoke detectors need to be installed in every room", 1),
  ("The whole flat has no power but the neighbours do", 3), ("A ceiling light flickers all the time", 1)],
 "heating_hvac": [
  ("The radiators are cold although the heating is on", 2), ("The boiler shows an error code and shuts down", 2),
  ("There is no heating at all and it is freezing outside", 3), ("The thermostat in the living room is not responding", 1),
  ("I need the yearly boiler service done", 0), ("The radiator in the bedroom makes loud gurgling noises", 0),
  ("I would like an air conditioning unit installed in the office room", 0), ("The underfloor heating only works in some rooms", 1),
  ("I can smell gas near the heating system", 3), ("The heat pump is running constantly but the flat stays cold", 2)],
 "carpentry": [
  ("The front door does not close properly anymore", 1), ("A window frame is rotten and the window will not shut", 2),
  ("I need a fitted wardrobe built in the bedroom alcove", 0), ("Several steps of the wooden staircase creak and one is loose", 1),
  ("I need shelves mounted in the study", 0), ("The kitchen worktop has swollen next to the sink", 1),
  ("An interior door is jammed and cannot be opened", 1), ("A balcony door was damaged in a break-in attempt", 3),
  ("I would like skirting boards fitted in the whole flat", 0), ("The garden gate has fallen off its hinges", 0)],
 "painting": [
  ("The living room walls need repainting", 0), ("There is black mould on the bathroom ceiling", 2),
  ("Wallpaper is peeling off in the bedroom", 0), ("I need the whole hallway painted before I move out", 1),
  ("Water damage left brown stains on the ceiling", 1), ("The window frames outside need to be repainted", 0),
  ("Cracks have appeared in the plaster of the stairwell", 1), ("I need the kitchen cabinets painted white", 0),
  ("The balcony railing is rusting and needs treatment", 1), ("The facade of our small house needs a fresh coat", 0)],
 "roofing": [
  ("Roof tiles were blown off in last night's storm", 3), ("There is a leak in the roof above the attic", 3),
  ("The gutters are overflowing and full of leaves", 1), ("I want a skylight installed in the loft conversion", 0),
  ("The flat roof of the garage is leaking after rain", 2), ("Moss has taken over the roof and tiles are cracking", 0),
  ("The chimney flashing looks damaged", 2), ("A downpipe has come loose from the wall", 1),
  ("Snow load damaged the roof edge of the carport", 2), ("I need the roof insulation checked before winter", 0)],
 "flooring": [
  ("I need laminate laid in two rooms", 0), ("The parquet floor is scratched and needs sanding", 0),
  ("Several bathroom tiles have cracked and are loose", 1), ("The carpet in the living room needs to be replaced with vinyl", 0),
  ("The floor has a bulge after a small water leak", 2), ("Grout between the kitchen tiles is crumbling", 0),
  ("The floor in the hallway is uneven and needs levelling", 1), ("A threshold strip between rooms has come loose and is a tripping hazard", 1),
  ("I need new tiles in the shower", 1), ("The old flooring must be removed and new floor laid", 0)],
 "appliance_repair": [
  ("The washing machine will not drain and shows an error", 1), ("The dishwasher leaves water at the bottom", 1),
  ("The fridge stopped cooling and the food is spoiling", 2), ("The oven does not heat up anymore", 1),
  ("The dryer takes forever and the clothes stay damp", 0), ("Two hobs on my induction cooker do not switch on", 1),
  ("I need a new extractor hood installed", 0), ("The freezer is covered in ice and has started to leak", 1),
  ("The washing machine is shaking violently and moving across the floor", 1), ("A new dishwasher needs to be connected", 0)],
 "locksmith": [
  ("I am locked out of my flat", 3), ("The key broke off inside the lock of the front door", 3),
  ("I want a higher-security cylinder fitted on the entrance door", 0), ("The lock on the cellar door is stiff and hard to turn", 0),
  ("After a break-in attempt the door lock is damaged", 3), ("I lost my keys and want all locks changed", 2),
  ("The letterbox lock is broken", 0), ("I need a smart lock installed", 0),
  ("The window handle lock is jammed", 1), ("I need a spare set of keys for the whole building entrance", 0)],
 "general_maintenance": [
  ("I need a TV mounted on the wall", 0), ("Several small jobs: curtain rails, pictures and a loose cupboard door", 0),
  ("The silicone around the bath has gone mouldy and needs replacing", 0), ("I need a flat-pack wardrobe assembled", 0),
  ("The garden fence has a few broken panels", 1), ("Door hinges are squeaking and the door sags", 0),
  ("A cupboard door in the kitchen fell off", 0), ("I need an overall check of the flat before handing it back to the landlord", 1),
  ("The balcony door does not seal and lets in the cold", 1), ("A handrail in the stairwell came loose", 2)],
}

OPENERS = ["", "", "Hello, ", "Hi, ", "Hello - ", "Good morning, ", "Hi there. ", "Need some help: "]
DETAILS = ["It started a couple of days ago.", "It happens every single time we use it.",
           "I have already tried to fix it myself without success.", "My landlord asked me to arrange the repair.",
           "I am at home most afternoons.", "I work from home so any day is fine.", "There are small children in the flat.",
           "It has been getting worse this week.", "I noticed it right after coming back from holiday.",
           "The previous repair did not last long.", "", ""]
URGENCY_TAILS = {"low": ["No rush at all.", "Any time in the next weeks is fine."],
                 "medium": ["Sometime this week would be great.", "I would like it sorted soon."],
                 "high": ["Please come as soon as possible.", "It is quite urgent for us."],
                 "critical": ["This is an emergency, please come immediately!", "Very urgent - I need someone right now."]}

FEEDBACK = {
 5: ["Excellent work, very professional.", "Fast, clean and fair price. Highly recommended!", "Arrived on time and solved the problem in no time.",
     "Perfect job, would book again.", "Friendly, competent and left everything tidy.", "Saved the day - thank you so much!"],
 4: ["Good job overall, small delay at the start.", "Problem fixed, a bit more expensive than expected.", "Solid work and friendly.",
     "Did what was needed, would use again.", "Professional, but arrived a little later than planned."],
 3: ["The work was okay but took longer than announced.", "Fixed the issue, though communication could be better.",
     "Average experience, the result is fine.", "Needed a second visit to finish the job."],
 2: ["Arrived very late and the repair did not last.", "Work was sloppy and I had to call someone else to finish.", "Not satisfied, the problem came back after two days."],
 1: ["Did not fix the problem and left a mess.", "Very unprofessional, would not recommend.", "Never showed up on time and the result was poor."],
}
FEEDBACK_EXTRAS = ["", "", "", " Good price for the work.", " Communication was easy.", " Explained everything clearly.", " Cleaned up afterwards."]

# Cell 6 — CV building blocks (used by 03_generate_handymen)
TITLES = {"plumbing": "Plumbing & Sanitary Technician", "electrical": "Electrician", "heating_hvac": "Heating & HVAC Technician",
          "carpentry": "Carpenter", "painting": "Painter & Decorator", "roofing": "Roofer", "flooring": "Flooring Specialist",
          "appliance_repair": "Appliance Service Technician", "locksmith": "Locksmith", "general_maintenance": "Property Maintenance Technician"}

EMPLOYER_WORDS = {"plumbing": ["Sanitaer", "Installationen", "Haustechnik"], "electrical": ["Elektrotechnik", "Elektro", "Elektroservice"],
                  "heating_hvac": ["Heizungsbau", "Klimatechnik", "Waermetechnik"], "carpentry": ["Schreinerei", "Tischlerei", "Holzbau"],
                  "painting": ["Malerbetrieb", "Farben & Putz", "Maler"], "roofing": ["Dachdeckerei", "Bedachungen", "Dach & Wand"],
                  "flooring": ["Bodenleger", "Parkett & Boden", "Fliesen & Boden"], "appliance_repair": ["Hausgeraete-Service", "Kundendienst", "Elektrogeraete"],
                  "locksmith": ["Schluesseldienst", "Sicherheitstechnik", "Schlosserei"], "general_maintenance": ["Hausmeisterservice", "Gebaeudeservice", "Facility Service"]}
EMPLOYER_SUFFIXES = ["GmbH", "GmbH & Co. KG", "e.K.", "& Soehne", "GbR", "Meisterbetrieb"]

CERTIFICATIONS = {
 "plumbing": ["Journeyman certificate - Anlagenmechaniker SHK", "Master craftsman (Meisterbrief) - Installateur und Heizungsbauer", "Drinking water hygiene certificate (VDI 6023)", "Welding certificate for copper pipes"],
 "electrical": ["Journeyman certificate - Elektroniker fuer Energie- und Gebaeudetechnik", "Master craftsman (Meisterbrief) - Elektrotechniker", "DGUV V3 testing of electrical installations", "Qualified electrician for EV charging infrastructure"],
 "heating_hvac": ["Journeyman certificate - Anlagenmechaniker SHK", "F-Gas certificate (Category I)", "Heat pump installer certificate", "Master craftsman (Meisterbrief) - Installateur und Heizungsbauer"],
 "carpentry": ["Journeyman certificate - Tischler", "Master craftsman (Meisterbrief) - Tischler", "Course: energy-efficient window installation (RAL)", "Timber construction safety certificate"],
 "painting": ["Journeyman certificate - Maler und Lackierer", "Master craftsman (Meisterbrief) - Maler und Lackierer", "Mould remediation certificate", "Facade insulation (WDVS) installer course"],
 "roofing": ["Journeyman certificate - Dachdecker", "Master craftsman (Meisterbrief) - Dachdecker", "Working at heights safety certificate", "Flat roof sealing certificate"],
 "flooring": ["Journeyman certificate - Parkettleger", "Journeyman certificate - Fliesen-, Platten- und Mosaikleger", "Master craftsman (Meisterbrief) - Raumausstatter", "Screed and levelling course"],
 "appliance_repair": ["Journeyman certificate - Elektroniker fuer Geraete und Systeme", "Manufacturer training (Bosch/Siemens home appliances)", "Refrigeration basics certificate", "Electrical safety testing of appliances"],
 "locksmith": ["Journeyman certificate - Metallbauer", "Certified locksmith and security technician", "Mechanical security technology course (VdS)", "Smart lock and access control training"],
 "general_maintenance": ["Certified caretaker (Objektbetreuer)", "First aid at work certificate", "Basic training: electrical installations for non-electricians", "Facility management course (IHK)"],
}
EDUCATION_SCHOOL = ["Hauptschulabschluss", "Realschulabschluss", "Realschulabschluss", "Fachhochschulreife", "Abitur"]
OTHER_CITIES    = ["Berlin", "Hamburg", "Bremen", "Saarbruecken", "Hannover", "Koeln", "Duesseldorf", "Muenchen", "Leipzig", "Dresden", "Kiel", "Luebeck", "Essen"]
LANGUAGES       = ["English", "English", "Turkish", "Polish", "Russian", "Italian", "French", "Arabic"]
