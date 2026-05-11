CREATE TABLE "dealer_pattern_bindings" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"dealer_id" uuid NOT NULL,
	"pattern_name" text NOT NULL,
	"params" jsonb NOT NULL,
	"freshness_sla" text,
	"schedule" text,
	"enabled" boolean DEFAULT true NOT NULL,
	"last_run_id" uuid,
	"last_run_sha256" text,
	"last_run_at" timestamp with time zone,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "dealers" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"name" text NOT NULL,
	"status" text NOT NULL,
	"inferred_make" text,
	"contact_email" text,
	"tier" text DEFAULT 'standard' NOT NULL,
	"onboarded_at" timestamp with time zone DEFAULT now() NOT NULL,
	"metadata" jsonb DEFAULT '{}'::jsonb
);
--> statement-breakpoint
CREATE TABLE "ingest_audit" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"run_id" uuid,
	"provider" text NOT NULL,
	"prompt_sha256" text NOT NULL,
	"prompt_template_ver" text NOT NULL,
	"response_text" text,
	"tokens_in" integer,
	"tokens_out" integer,
	"cost_usd" numeric(10, 6),
	"latency_ms" integer,
	"cache_hit" boolean NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "ingest_runs" (
	"run_id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"dealer_id" uuid,
	"source_file" text NOT NULL,
	"source_sha256" text NOT NULL,
	"status" text NOT NULL,
	"rows_attempted" integer,
	"rows_succeeded" integer,
	"rows_failed" integer,
	"llm_calls" integer,
	"llm_cost_usd" numeric(10, 4),
	"started_at" timestamp with time zone DEFAULT now() NOT NULL,
	"finished_at" timestamp with time zone,
	"error" text,
	"reason" text
);
--> statement-breakpoint
CREATE TABLE "ingestion_patterns" (
	"pattern_name" text PRIMARY KEY NOT NULL,
	"pattern_type" text NOT NULL,
	"handler_module" text NOT NULL,
	"schema_signature" jsonb NOT NULL,
	"validation_rules" jsonb NOT NULL,
	"default_freshness_sla" text,
	"default_schedule" text,
	"version" integer NOT NULL,
	"deprecated_at" timestamp with time zone,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "part_number_aliases" (
	"product_id" bigint NOT NULL,
	"alias" text NOT NULL,
	"alias_norm" text NOT NULL,
	"alias_type" text NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "part_number_aliases_alias_norm_product_id_pk" PRIMARY KEY("alias_norm","product_id")
);
--> statement-breakpoint
CREATE TABLE "product_images" (
	"product_id" bigint NOT NULL,
	"r2_key" text NOT NULL,
	"r2_url" text NOT NULL,
	"sha256" text NOT NULL,
	"width_px" integer,
	"height_px" integer,
	"section_label" text,
	"source_sheet" text,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "product_images_product_id_sha256_pk" PRIMARY KEY("product_id","sha256")
);
--> statement-breakpoint
CREATE TABLE "products" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"part_number" text NOT NULL,
	"part_number_norm" text GENERATED ALWAYS AS (upper(regexp_replace(part_number, 's', '', 'g'))) STORED,
	"name_en" text,
	"name_cn" text,
	"spec_cn" text,
	"qty_per_vehicle" numeric,
	"dealer_cost" numeric,
	"unit" text,
	"retail_price" numeric,
	"fitment" jsonb DEFAULT '[]'::jsonb NOT NULL,
	"primary_image_r2_key" text,
	"source_dealer_id" uuid,
	"source_file_sha256" text,
	"source_sheet" text,
	"source_row_index" integer,
	"data_quality" jsonb DEFAULT '{}'::jsonb,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "reference_specs" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"category" text NOT NULL,
	"model_code" text,
	"attributes" jsonb NOT NULL,
	"source_sheet" text,
	"source_row" integer,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "stream_events" (
	"event_id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"dealer_id" uuid NOT NULL,
	"event_type" text NOT NULL,
	"payload" jsonb NOT NULL,
	"source" text,
	"received_at" timestamp with time zone DEFAULT now() NOT NULL,
	"processed_at" timestamp with time zone,
	"status" text NOT NULL,
	"error" text
);
--> statement-breakpoint
CREATE TABLE "stream_outbox" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"topic" text NOT NULL,
	"payload" jsonb NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"published_at" timestamp with time zone,
	"status" text DEFAULT 'PENDING' NOT NULL
);
--> statement-breakpoint
CREATE TABLE "vehicle_models" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"make" text NOT NULL,
	"model" text NOT NULL,
	"model_code" text,
	"category" text,
	"year_start" integer,
	"year_end" integer,
	"variant" text,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
ALTER TABLE "dealer_pattern_bindings" ADD CONSTRAINT "dealer_pattern_bindings_dealer_id_dealers_id_fk" FOREIGN KEY ("dealer_id") REFERENCES "public"."dealers"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "dealer_pattern_bindings" ADD CONSTRAINT "dealer_pattern_bindings_pattern_name_ingestion_patterns_pattern_name_fk" FOREIGN KEY ("pattern_name") REFERENCES "public"."ingestion_patterns"("pattern_name") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "dealer_pattern_bindings" ADD CONSTRAINT "dealer_pattern_bindings_last_run_id_ingest_runs_run_id_fk" FOREIGN KEY ("last_run_id") REFERENCES "public"."ingest_runs"("run_id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "ingest_audit" ADD CONSTRAINT "ingest_audit_run_id_ingest_runs_run_id_fk" FOREIGN KEY ("run_id") REFERENCES "public"."ingest_runs"("run_id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "part_number_aliases" ADD CONSTRAINT "part_number_aliases_product_id_products_id_fk" FOREIGN KEY ("product_id") REFERENCES "public"."products"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "product_images" ADD CONSTRAINT "product_images_product_id_products_id_fk" FOREIGN KEY ("product_id") REFERENCES "public"."products"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
CREATE UNIQUE INDEX "ux_bindings_dealer_pattern" ON "dealer_pattern_bindings" USING btree ("dealer_id","pattern_name");--> statement-breakpoint
CREATE INDEX "ix_bindings_enabled_lastrun" ON "dealer_pattern_bindings" USING btree ("enabled","last_run_at");--> statement-breakpoint
CREATE INDEX "ix_audit_run_time" ON "ingest_audit" USING btree ("run_id","created_at");--> statement-breakpoint
CREATE INDEX "ix_runs_status_started" ON "ingest_runs" USING btree ("status","started_at");--> statement-breakpoint
CREATE INDEX "ix_runs_dealer_started" ON "ingest_runs" USING btree ("dealer_id","started_at");--> statement-breakpoint
CREATE INDEX "ix_aliases_norm" ON "part_number_aliases" USING btree ("alias_norm");--> statement-breakpoint
CREATE UNIQUE INDEX "ux_products_partnum_dealer" ON "products" USING btree ("part_number_norm","source_dealer_id");--> statement-breakpoint
CREATE INDEX "ix_products_fitment_gin" ON "products" USING gin ("fitment" jsonb_path_ops);--> statement-breakpoint
CREATE INDEX "ix_products_name_en_trgm" ON "products" USING gin ("name_en" gin_trgm_ops);--> statement-breakpoint
CREATE INDEX "ix_products_name_cn_trgm" ON "products" USING gin ("name_cn" gin_trgm_ops);--> statement-breakpoint
CREATE INDEX "ix_refspecs_category_model" ON "reference_specs" USING btree ("category","model_code");--> statement-breakpoint
CREATE INDEX "ix_stream_events_dealer_status" ON "stream_events" USING btree ("dealer_id","status","received_at");--> statement-breakpoint
CREATE INDEX "ix_outbox_pending" ON "stream_outbox" USING btree ("created_at") WHERE status = 'PENDING';--> statement-breakpoint
CREATE UNIQUE INDEX "ux_vehicle_models_identity" ON "vehicle_models" USING btree ("make","model_code","year_start","year_end","variant");--> statement-breakpoint
CREATE INDEX "ix_vehicle_models_category" ON "vehicle_models" USING btree ("category");