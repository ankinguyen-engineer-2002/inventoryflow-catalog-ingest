-- silver_fitment_atomic
--
-- Atomic fitment relationships. One row per (part_number, vehicle_model_code).
-- Derived from the source_sheet name which encodes vehicle and year range
-- (e.g., "FOXStorm 70 AY70-2", "Bull 180 AU180 (2020-2022)").
--
-- The dbt model here demonstrates the gold-layer fan-in target. Production
-- silver would invoke the ported `resolveFitmentFromSheetName` regex
-- grammar via a Python model rather than SQL regex.

{{
  config(
    materialized='table',
    schema='silver',
    tags=['catalog', 'silver', 'fitment']
  )
}}

with parts as (
    select dealer_id, part_number, primary_source_sheet
    from {{ ref('silver_parts_atomic') }}
),

vehicle_extracted as (

    select
        dealer_id,
        part_number,
        primary_source_sheet,
        -- Extract model code prefix from sheet name.
        regexp_extract(
            primary_source_sheet,
            '(AY|AT|AU|KMB|TS|TSD|TD|TT|K2|K4|K6|KT|T2|T4|S70|S200|S350|eA|eKMB)[0-9]*-?[0-9]*',
            0
        )                                                AS model_code,
        -- Year range: prefer parenthesised range; fall back to single year.
        try_cast(
            regexp_extract(primary_source_sheet,
                           '\(([0-9]{4})\s*-\s*([0-9]{4})\)', 1) AS integer
        )                                                AS year_start,
        try_cast(
            regexp_extract(primary_source_sheet,
                           '\(([0-9]{4})\s*-\s*([0-9]{4})\)', 2) AS integer
        )                                                AS year_end,
        -- Variant: EPA, EFI markers.
        regexp_extract(primary_source_sheet, '\b(EPA|EFI)\b', 1) AS variant
    from parts

)

select
    dealer_id,
    part_number,
    model_code,
    year_start,
    year_end,
    variant,
    'Kayo' AS make,                                       -- Sourced from dealers.inferred_make in production
    primary_source_sheet
from vehicle_extracted
where model_code is not null
  and model_code <> ''
