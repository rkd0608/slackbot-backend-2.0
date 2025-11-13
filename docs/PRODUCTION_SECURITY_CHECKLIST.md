# Production Security Checklist

This document outlines all security measures that must be implemented before launching to production.

## ✅ Completed (P0 - Launch Blockers)

### 1. Token Logging Removed
- **Status**: ✅ Complete
- **Files Modified**: `app/services/oauth_service.py`
- **What**: Removed OAuth state tokens from application logs
- **Why**: Prevents token leakage in centralized logging systems

### 2. S3 Server-Side Encryption
- **Status**: ✅ Complete
- **Files Modified**: `app/core/storage.py`
- **What**: All S3 uploads use `ServerSideEncryption='AES256'`
- **Why**: Encrypts files at rest (Slack files, extracted text)

### 3. File Upload Size Limits
- **Status**: ✅ Complete
- **Files Modified**:
  - `app/core/config.py` (added `MAX_FILE_SIZE_MB=50`)
  - `app/services/file_processor.py` (size validation)
  - `.env.example` (documentation)
- **What**: Rejects files larger than 50MB
- **Why**: Prevents DoS attacks via large file uploads

### 4. Input Validation Middleware
- **Status**: ✅ Complete
- **Files Created**: `app/core/validation.py`
- **Files Modified**: `app/api/commands.py`
- **What**: Validates and sanitizes:
  - User queries (SQL injection, XSS prevention)
  - Slack IDs (team_id, user_id, channel_id format)
  - File paths (path traversal prevention)
- **Why**: Blocks malicious input patterns

### 5. AWS Secrets Manager Integration
- **Status**: ✅ Complete (code ready, deployment pending)
- **Files Created**:
  - `app/core/secrets.py`
  - `docs/SECRETS_MANAGER_SETUP.md`
- **Files Modified**:
  - `app/core/config.py`
  - `.env.example`
- **What**: Retrieves secrets from AWS Secrets Manager in production
- **Why**: Eliminates .env files in production, enables secret rotation
- **Deployment**: Follow `docs/SECRETS_MANAGER_SETUP.md`

## 🔧 Requires Configuration (P0 - Manual Steps)

### 6. MySQL Encryption at Rest
- **Status**: ⚠️ Requires configuration
- **What**: Enable MySQL transparent data encryption (TDE)
- **How**:

  **For AWS RDS:**
  ```bash
  # Enable encryption when creating RDS instance
  aws rds create-db-instance \
    --db-instance-identifier slackbot-prod \
    --storage-encrypted \
    --kms-key-id arn:aws:kms:region:account:key/key-id
  ```

  **For Docker (development only):**
  MySQL 8.0+ has encryption at rest, but requires configuration:
  ```sql
  -- Check encryption status
  SHOW VARIABLES LIKE 'innodb_encryption%';

  -- Enable tablespace encryption
  ALTER TABLESPACE slack_intelligence ENCRYPTION='Y';
  ```

  **For Production:** Use managed service (AWS RDS, Google Cloud SQL) with encryption enabled.

- **Verification**:
  ```sql
  SELECT SCHEMA_NAME, DEFAULT_ENCRYPTION
  FROM INFORMATION_SCHEMA.SCHEMATA
  WHERE SCHEMA_NAME = 'slack_intelligence';
  ```

### 7. Redis Password Authentication
- **Status**: ⚠️ Requires configuration
- **What**: Enable password authentication for Redis
- **How**:

  **Update docker-compose.yml:**
  ```yaml
  redis:
    image: redis:7-alpine
    command: redis-server --requirepass ${REDIS_PASSWORD}
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
  ```

  **Update .env:**
  ```
  REDIS_PASSWORD=your-strong-redis-password-min-32-chars
  ```

  **For AWS ElastiCache:**
  ```bash
  aws elasticache create-replication-group \
    --replication-group-id slackbot-redis \
    --auth-token "your-strong-password" \
    --transit-encryption-enabled \
    --at-rest-encryption-enabled
  ```

- **Verification**:
  ```bash
  redis-cli -a $REDIS_PASSWORD ping
  # Should return PONG
  ```

### 8. HTTPS/TLS Enforcement
- **Status**: ⚠️ Requires DevOps setup
- **What**: Enforce HTTPS for all API endpoints
- **How**:

  **Option A: AWS Load Balancer**
  ```bash
  # Create ACM certificate
  aws acm request-certificate \
    --domain-name api.yourcompany.com \
    --validation-method DNS

  # Attach to load balancer
  aws elbv2 create-listener \
    --load-balancer-arn <alb-arn> \
    --protocol HTTPS \
    --port 443 \
    --certificates CertificateArn=<cert-arn> \
    --default-actions Type=forward,TargetGroupArn=<tg-arn>

  # Redirect HTTP to HTTPS
  aws elbv2 create-listener \
    --load-balancer-arn <alb-arn> \
    --protocol HTTP \
    --port 80 \
    --default-actions Type=redirect,RedirectConfig={Protocol=HTTPS,Port=443,StatusCode=HTTP_301}
  ```

  **Option B: Nginx Reverse Proxy**
  ```nginx
  server {
      listen 80;
      server_name api.yourcompany.com;
      return 301 https://$server_name$request_uri;
  }

  server {
      listen 443 ssl http2;
      server_name api.yourcompany.com;

      ssl_certificate /etc/ssl/certs/cert.pem;
      ssl_certificate_key /etc/ssl/private/key.pem;
      ssl_protocols TLSv1.2 TLSv1.3;
      ssl_ciphers HIGH:!aNULL:!MD5;

      location / {
          proxy_pass http://localhost:8000;
          proxy_set_header Host $host;
          proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
          proxy_set_header X-Forwarded-Proto $scheme;
      }
  }
  ```

- **Verification**:
  ```bash
  curl -I https://api.yourcompany.com/health
  # Should return 200 OK

  curl -I http://api.yourcompany.com/health
  # Should redirect to HTTPS (301)
  ```

## 🔒 Additional Security Measures (P1 - Week 1)

### 9. Database Connection Encryption
- Enable SSL/TLS for MySQL connections
- Update SQLAlchemy connection string:
  ```python
  connect_args={"ssl": {"ssl_ca": "/path/to/ca-cert.pem"}}
  ```

### 10. Rate Limiting
- Already configured in `app/core/config.py`:
  ```python
  query_rate_limit_per_hour: int = 100
  query_burst_limit_per_minute: int = 10
  ```
- Implement enforcement in middleware

### 11. CORS Configuration
- Restrict allowed origins to production frontend domain
- Update FastAPI CORS middleware:
  ```python
  app.add_middleware(
      CORSMiddleware,
      allow_origins=["https://app.yourcompany.com"],
      allow_credentials=True,
      allow_methods=["GET", "POST"],
      allow_headers=["*"],
  )
  ```

### 12. Security Headers
- Add security headers middleware:
  ```python
  @app.middleware("http")
  async def add_security_headers(request, call_next):
      response = await call_next(request)
      response.headers["X-Content-Type-Options"] = "nosniff"
      response.headers["X-Frame-Options"] = "DENY"
      response.headers["X-XSS-Protection"] = "1; mode=block"
      response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
      return response
  ```

### 13. Slack Signature Verification
- **Status**: ✅ Already implemented
- Located in: `app/core/slack_verification.py`
- Verifies all Slack webhook requests

### 14. JWT Token Security
- **Status**: ✅ Already implemented
- Located in: `app/core/auth.py`
- Uses HS256 algorithm with secure secret key

## 📊 Monitoring & Auditing (P1)

### 15. CloudTrail Logging
- Enable AWS CloudTrail for all API calls
- Monitor Secrets Manager access

### 16. Application Logging
- **Status**: ✅ Structured logging implemented
- Review logs for security events:
  - Failed authentication attempts
  - Invalid input attempts
  - Rate limit violations

### 17. Prometheus Metrics
- **Status**: ✅ Prometheus integration ready
- Monitor security metrics:
  - Failed login attempts
  - Large file upload attempts
  - Input validation failures

## 🔄 Secret Rotation Schedule (P1)

| Secret | Rotation Frequency | Automated? |
|--------|-------------------|------------|
| Slack Bot Token | 90 days | Manual |
| Database Password | 30 days | Via AWS Secrets Manager |
| JWT Secret | 90 days | Manual |
| API Keys (OpenAI, etc.) | 90 days | Manual |
| Redis Password | 30 days | Manual |
| RabbitMQ Password | 30 days | Manual |

## 🚀 Pre-Launch Checklist

Complete these steps before production deployment:

- [ ] **Secrets Manager**
  - [ ] Create secret in AWS Secrets Manager
  - [ ] Configure IAM role for application
  - [ ] Set `USE_SECRETS_MANAGER=true`
  - [ ] Test secret retrieval
  - [ ] Delete .env file from production server

- [ ] **Database Security**
  - [ ] Enable MySQL encryption at rest
  - [ ] Configure SSL/TLS for connections
  - [ ] Restrict database access to application security group

- [ ] **Redis Security**
  - [ ] Set strong Redis password (32+ chars)
  - [ ] Enable AUTH in Redis config
  - [ ] Enable encryption at rest (AWS ElastiCache)

- [ ] **Network Security**
  - [ ] Configure security groups (least privilege)
  - [ ] Enable VPC for all services
  - [ ] Disable public access to MySQL, Redis, RabbitMQ
  - [ ] Enable HTTPS/TLS for all endpoints

- [ ] **Monitoring**
  - [ ] Enable CloudWatch logging
  - [ ] Set up CloudTrail
  - [ ] Configure alerts for failed auth attempts
  - [ ] Configure alerts for unusual traffic patterns

- [ ] **Compliance**
  - [ ] Review GDPR requirements
  - [ ] Implement data retention policy
  - [ ] Configure backup encryption
  - [ ] Document incident response plan

## 📝 Post-Launch

Within 30 days of launch:

- [ ] Conduct security audit
- [ ] Perform penetration testing
- [ ] Review access logs
- [ ] Rotate all secrets
- [ ] Update security documentation

## 🆘 Incident Response

If a security incident occurs:

1. **Immediate**:
   - Rotate all secrets immediately
   - Review access logs for compromised period
   - Isolate affected systems

2. **Investigation**:
   - Analyze CloudTrail logs
   - Review application logs
   - Identify scope of breach

3. **Notification**:
   - Notify affected users (if applicable)
   - Report to regulatory bodies (if required)
   - Document incident for post-mortem

4. **Recovery**:
   - Implement additional security measures
   - Update incident response plan
   - Conduct security training

## 📚 References

- [OWASP Top 10](https://owasp.org/www-project-top-ten/)
- [AWS Security Best Practices](https://docs.aws.amazon.com/security/)
- [Slack Security Best Practices](https://api.slack.com/authentication/best-practices)
- [FastAPI Security](https://fastapi.tiangolo.com/tutorial/security/)
