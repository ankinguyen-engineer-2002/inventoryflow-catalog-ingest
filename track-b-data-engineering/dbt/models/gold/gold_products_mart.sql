-- gold_products_mart
--
-- Denormalised products mart matching Track A's PostgreSQL `products`
-- schema. The `fitment` column is aggregated to a JSON array using
-- DuckDB's json_group_array — the same wire format that downstream
-- consumers (eBay, Amazon, Google Shopping catalog APIs) accept.

{{
  config(
    materialized='table',
    schema='gold',
    tags=['catalog', 'gold', 'serving']
  )
}}

with parts as (
    select * from {{ ref('silver_parts_atomic') }}
),

fitment as (
    select * from {{ ref('silver_fitment_atomic') }}
),

fitment_aggregated as (

    select
        dealer_id,
        part_number,
        json_group_array(
            json_object(
                'year',       coalesce(year_start, 0),
                'make',       make,
                'model_code', model_code,
                'variant',    variant,
                'confidence', 'high'
            )
        )                                                  AS fitment_json
    from fitment
    group by dealer_id, part_number

)

select
    p.dealer_id,
    p.part_number,
    p.name_en,
    p.name_cn,
    p.retail_price,
    coalesce(f.fitment_json, '[]')                         AS fitment,
    p.last_seen_date
from parts p
left join fitment_aggregated f
       on f.dealer_id   = p.dealer_id
      and f.part_number = p.part_number
