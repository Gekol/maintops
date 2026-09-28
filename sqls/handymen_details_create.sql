CREATE TABLE "maintops"."handyman_details" (
	"user_id" bigint PRIMARY KEY,
	"specialisations" text[],
	"skills" text[],
	"experience_summary" text,
	"cv_path" text,
	"cv_raw_text" text,
	"has_car" boolean DEFAULT false NOT NULL,
	"completed_cases" integer DEFAULT 0 NOT NULL,
	"rating_avg" numeric(3, 2) DEFAULT '0' NOT NULL,
	"rating_count" integer DEFAULT 0 NOT NULL,
	"avg_price" numeric(10, 2),
	CONSTRAINT "chk_handyman_completed_cases" CHECK ((completed_cases >= 0)),
	CONSTRAINT "chk_handyman_rating" CHECK (((rating_avg >= (0)::numeric) AND (rating_avg <= (5)::numeric))),
	CONSTRAINT "chk_handyman_rating_count" CHECK ((rating_count >= 0))
);
CREATE UNIQUE INDEX "handyman_details_pkey" ON "maintops"."handyman_details" ("user_id");
ALTER TABLE "maintops"."handyman_details" ADD CONSTRAINT "fk_handyman_user" FOREIGN KEY ("user_id") REFERENCES "maintops"."users"("id");