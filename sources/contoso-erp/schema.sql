-- Contoso ERP, as the finance system actually stores it.
--
-- This is NOT a change log. It is the CURRENT STATE of an account, one row per
-- customer, exactly as an ERP would hold it. The change log is what Debezium
-- produces by watching this table — which is the difference between simulating
-- CDC and doing it.
--
-- `effective_date` is the BUSINESS date a change took effect, carried as data.
-- It deliberately disagrees with capture order for a share of rows: a connector
-- that retried, or a back-dated correction posted late. A pipeline that orders
-- by the wrong one gets the wrong current state, and the fixture is built so
-- that mistake shows up rather than hiding.
CREATE SCHEMA IF NOT EXISTS erp;

CREATE TABLE IF NOT EXISTS erp.customer (
    erp_customer_id     text PRIMARY KEY,
    phone               text NOT NULL,
    legal_name          text NOT NULL,
    account_tier        text NOT NULL,
    segment             text NOT NULL,
    credit_band         text NOT NULL,
    account_status      text NOT NULL,
    payment_terms_days  integer NOT NULL,
    country             text NOT NULL,
    effective_date      date NOT NULL
);

-- REPLICA IDENTITY FULL, deliberately. The default (primary key only) means a
-- DELETE event carries nothing but the id, and an SCD2 build cannot close the
-- version it belonged to without the row's attributes. A delete that erases the
-- past is exactly the failure the ERP source exists to teach about.
ALTER TABLE erp.customer REPLICA IDENTITY FULL;
