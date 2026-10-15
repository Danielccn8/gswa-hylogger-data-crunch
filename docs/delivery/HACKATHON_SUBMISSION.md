# GSWA Hackathon 2026 submission

## Team

Group 7 UWA Capstone

Captain: Daniel Cartagena, @Danielccn8

Members:
- Avantika Naudiyal, @24525586AV (TIR baseline pull-request account)
- Daniel Cartagena, @Danielccn8
- Jovan Aurelius Wylen, @jovanaurelius
- Sathi Padippingal, @padippingalsathi-svg
- Yeshwanth Reddy G Gunnamreddy, @Yashu1708

## Challenge theme

Data Crunch

## Project repository

https://github.com/Danielccn8/gswa-hylogger-data-crunch

## Project summary

HyLogger Explorer helps geologists compare mineral and spectral patterns across
Western Australian drill holes. The application combines public data acquisition,
quality checks and exploratory analysis in a local browser interface. Users can
inspect depth profiles, compare similar holes and investigate unusual intervals
with supporting quality information.

The corrected 14 October analysis groups 289,266 eligible SWIR intervals from
1,272 holes and 353,059 eligible TIR intervals from 1,224 holes. The combined
processed whole-hole view compares 1,100 holes with both sensors. Earlier team
experiments explored a 20-hole processed TIR cohort and a 15-hole raw spectral
cohort. These are separate experiments with different methods and populations.

The practical benefit is a consistent starting point for choosing holes and
intervals for geological review. Clusters are exploratory mineral patterns, not
confirmed geological classifications or ore-grade predictions. The Windows
application runs without installing Python or Docker, with resumable, opt-in
collection of public data.

## Data used

WA HyLogger scalar logs from NVCL Data Services and spectral packages from the
WA NCI THREDDS catalogue. Local research also used the GSWA interim HyLogger
Drillholes package for reference metadata and comparisons with assays.

Sources:
- https://thredds.nci.org.au/thredds/catalog/rs07/WA/catalog.html
- https://dasc.dmirs.wa.gov.au/ (HyLogger Drillholes package)
- https://github.com/AuScope/nvcl_kit
- https://github.com/Geological-Survey-of-Western-Australia/pytsg

Pilot hole selections are in configs/selections/. The raw pilot used 13TRDD012,
13TRDD013, 15RCDVZ001, 16MYD0047, 22USDH0207, EPT2276, FDH002, KEDD002,
LD82625W1, PND003, PND004, PND005, RRLGDDD093, SPBD0364 and WKDD15001.

The public application does not bundle geological datasets, the local research
database or the reference package. A new installation must acquire and prepare
public data before showing populated analysis views.

## Tools and technologies

Python, NumPy, pandas, scikit-learn, pytsg, nvcl-kit, FastAPI, PostgreSQL/PostGIS,
Leaflet and PyInstaller.

## AI usage declaration

Yes. AI tools assisted software development, review, documentation and presentation
preparation. The team is responsible for the submission and interpretation of results.

## Pitch slides

Attach the final PDF, HyLogger Explorer - GSWA Hackathon 2026.pdf, to the submission
form. It contains the final clustering counts and screenshots of the populated
local application. The editable PPTX is available in the local delivery folder.

## Application

https://github.com/Danielccn8/gswa-hylogger-data-crunch/releases/tag/explorer-v2-2026.10.14

## Captain's final checks

Confirm the member account spelling, attach the final PDF, review the form's data
and terms declarations, and submit before 15 October 2026 at 11:59 pm AWST.
This document is prepared submission text, not evidence of a submitted entry.
