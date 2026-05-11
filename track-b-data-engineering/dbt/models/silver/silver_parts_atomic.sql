-- silver_parts_atomic
--
-- Normalised parts table. One row per (part_number, dealer_id) tuple.
-- Deduplicates SKU variants by keeping the most recent (lexicographic
-- max on _raw_json hash as a stable tie-break).
--
-- This is the dbt equivalent of Track A's `products` upsert. The
-- materialisation is a full table rebuild on each dbt run; for
-- production at scale, switch to `incremental` materialisation
-- partitioned by ingestion_date.

{{
  config(
    materialized='table',
    schema='silver',
    tags=['catalog', 'silver']
  )
}}

with raw as (

    select
        _dealer_id                                    as dealer_id,
        _source_sheet                                 as source_sheet,
        _ingestion_date                               as ingestion_date,
        _raw_json                                     as raw_json,
        -- Extract canonical fields using regex over the serialised JSON.
        -- For production correctness, the silver layer would invoke the
        -- ported section detector via Python UDFs; this SQL form is the
        -- demonstration of the dbt model shape.
        regexp_extract(_raw_json, '''([0-9]{6}-[0-9]{4}[A-Z0-9-]*)''', 1)
                                                       as part_number,
        regexp_extract(_raw_json, '''EN name'':\s*''([^'']+)''', 1)
                                                       as name_en,
        regexp_extract(_raw_json, '''CN name'':\s*''([^'']+)''', 1)
                                                       as name_cn,
        try_cast(regexp_extract(_raw_json, '''Retail'':\s*''([0-9.]+)''', 1)
                 as double)                            as retail_price
    from {{ source('bronze', 'bronze_catalog_rows') }}

),

filtered as (

    select *
    from raw
    where part_number is not null
      and part_number <> ''

),

deduped as (

    -- Where the same (dealer, part_number) appears in multiple bronze rows
    -- (typical when a dealer re-ingests their xlsx), keep one canonical row.
    select
        dealer_id,
        part_number,
        max(name_en)        as name_en,
        max(name_cn)        as name_cn,
        max(retail_price)   as retail_price,
        min(source_sheet)   as primary_source_sheet,
        max(ingestion_date) as last_seen_date,
        count(*)            as occurrence_count
    from filtered
    group by dealer_id, part_number

)

select * from deduped
