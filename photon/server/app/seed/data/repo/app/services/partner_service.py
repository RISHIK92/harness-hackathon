from __future__ import annotations

# Partner-tier accounts get preferential commission rates in specific cities
# where Meridian has a reseller/referral agreement in place. See
# app.pricing.PARTNER_CITY_RATES for the actual multipliers applied at
# settlement time. New partner cities go through Partnerships + Finance
# sign-off before a rate entry is added here.
PARTNER_CITIES = {"Bangalore"}


def is_partner_eligible_city(city: str) -> bool:
    return city in PARTNER_CITIES
