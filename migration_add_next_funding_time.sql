-- Migration: add per-exchange next_funding_time for FF cards
-- (next_funding_time_bid / next_funding_time_ask), only populated for strategy='ff'.
-- next_funding_time (already added in the previous migration) stays as the
-- min() of both — used for the card footer summary.
-- Run this once against the existing "funding" database.

ALTER TABLE funding_opportunities
    ADD COLUMN IF NOT EXISTS next_funding_time_bid TIMESTAMPTZ;

ALTER TABLE funding_opportunities
    ADD COLUMN IF NOT EXISTS next_funding_time_ask TIMESTAMPTZ;