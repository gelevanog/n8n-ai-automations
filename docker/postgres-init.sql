-- One PostgreSQL server for the demo: Relay's ops store (the default database), n8n's own database and the mock
-- third-party systems. A production deployment would give n8n its own server.
CREATE DATABASE n8n;
CREATE DATABASE mocks;
