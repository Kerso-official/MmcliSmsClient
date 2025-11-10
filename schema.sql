-- SqLite3 schema for storing sms messaging history
-- ofc without any personal data

CREATE TABLE IF NOT EXISTS history (
    id SERIAL PRIMARY KEY,
    tel VARCHAR(15) NOT NULL,
    last_message TIMESTAMP NOT NULL
);

