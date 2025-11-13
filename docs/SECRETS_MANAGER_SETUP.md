# AWS Secrets Manager Setup Guide

This guide explains how to set up AWS Secrets Manager for secure credential management in production.

## Why Use AWS Secrets Manager?

- **Security**: Secrets are encrypted at rest using AWS KMS
- **Rotation**: Automatic secret rotation with AWS Lambda
- **Audit**: CloudTrail logs all secret access
- **No .env files**: Eliminates risk of committing secrets to version control

## Prerequisites

1. AWS account with appropriate IAM permissions
2. AWS CLI installed and configured
3. Application deployed with IAM role that has Secrets Manager access

## Step 1: Create IAM Policy

Create an IAM policy for Secrets Manager access:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "secretsmanager:GetSecretValue",
        "secretsmanager:DescribeSecret"
      ],
      "Resource": "arn:aws:secretsmanager:us-east-1:ACCOUNT_ID:secret:slackbot/production-*"
    }
  ]
}
```

Attach this policy to your ECS task role, EC2 instance role, or Lambda execution role.

## Step 2: Create Secret in AWS Secrets Manager

### Option A: Using AWS Console

1. Go to AWS Secrets Manager console
2. Click "Store a new secret"
3. Select "Other type of secret"
4. Click "Plaintext" tab
5. Paste the JSON structure below (fill in your actual values)
6. Name the secret: `slackbot/production`
7. Click "Store"

### Option B: Using AWS CLI

Create a file `secret.json` with your credentials:

```json
{
  "SLACK_BOT_TOKEN": "xoxb-your-actual-bot-token",
  "SLACK_APP_TOKEN": "xapp-your-actual-app-token",
  "SLACK_SIGNING_SECRET": "your-actual-signing-secret",
  "SLACK_CLIENT_ID": "your-client-id",
  "SLACK_CLIENT_SECRET": "your-client-secret",

  "MYSQL_USER": "your-mysql-user",
  "MYSQL_PASSWORD": "your-mysql-password",
  "MYSQL_DATABASE": "slack_intelligence",

  "OPENAI_API_KEY": "sk-your-openai-api-key",
  "PINECONE_API_KEY": "your-pinecone-api-key",

  "AWS_ACCESS_KEY_ID": "your-aws-access-key",
  "AWS_SECRET_ACCESS_KEY": "your-aws-secret-key",

  "JWT_SECRET_KEY": "your-jwt-secret-min-32-chars",

  "STRIPE_SECRET_KEY": "sk_live_your-stripe-key",
  "STRIPE_WEBHOOK_SECRET": "whsec_your-webhook-secret",

  "REDIS_PASSWORD": "your-redis-password",
  "RABBITMQ_PASSWORD": "your-rabbitmq-password"
}
```

Then create the secret:

```bash
aws secretsmanager create-secret \
  --name slackbot/production \
  --description "Production secrets for Slack bot" \
  --secret-string file://secret.json \
  --region us-east-1
```

**IMPORTANT**: Delete `secret.json` immediately after creating the secret!

```bash
shred -u secret.json  # Linux
rm -P secret.json     # macOS
```

## Step 3: Enable Secrets Manager in Application

Update your production environment variables:

```bash
USE_SECRETS_MANAGER=true
AWS_SECRET_NAME=slackbot/production
AWS_REGION=us-east-1
```

## Step 4: Update Requirements

Add boto3 to your requirements.txt (should already be included):

```
boto3>=1.28.0
```

## Step 5: Verify Setup

Test that your application can retrieve secrets:

```bash
# In your production environment
docker-compose exec app python -c "
from app.core.secrets import secrets_manager
secrets_manager.initialize()
slack_token = secrets_manager.get_secret('SLACK_BOT_TOKEN')
print('✓ Secrets Manager working!' if slack_token else '✗ Failed to retrieve secret')
"
```

## Secret Rotation

### Manual Rotation

Update a secret value:

```bash
aws secretsmanager update-secret \
  --secret-id slackbot/production \
  --secret-string file://updated-secret.json \
  --region us-east-1
```

Refresh secrets in running application (requires app restart or cache refresh):

```bash
docker-compose restart app workers
```

### Automatic Rotation (Advanced)

For automatic rotation, set up a Lambda function:

1. Create Lambda function for rotation logic
2. Configure rotation schedule in Secrets Manager
3. Update secret rotation configuration:

```bash
aws secretsmanager rotate-secret \
  --secret-id slackbot/production \
  --rotation-lambda-arn arn:aws:lambda:region:account:function:rotate-slackbot-secrets \
  --rotation-rules AutomaticallyAfterDays=30
```

## Development vs Production

### Development (Local)
- `USE_SECRETS_MANAGER=false` (default)
- Uses `.env` file
- Fast iteration, no AWS dependencies

### Production
- `USE_SECRETS_MANAGER=true`
- Uses AWS Secrets Manager
- Secure, auditable, no files with secrets

## Security Best Practices

1. **Never commit secrets to git**
   - Add `.env` to `.gitignore` (already done)
   - Use `.env.example` for structure only

2. **Rotate secrets regularly**
   - Rotate Slack tokens every 90 days
   - Rotate database passwords every 30 days
   - Rotate API keys when team members leave

3. **Use IAM roles, not access keys**
   - ECS tasks: Use task execution role
   - EC2: Use instance profile
   - Lambda: Use execution role

4. **Monitor access**
   - Enable CloudTrail logging
   - Set up CloudWatch alerts for secret access
   - Review access logs monthly

5. **Principle of least privilege**
   - Only grant `GetSecretValue` permission
   - Restrict to specific secret ARN
   - Use separate secrets for dev/staging/prod

## Troubleshooting

### Error: "ResourceNotFoundException"
- Secret name is incorrect
- Secret doesn't exist in specified region
- Check AWS_SECRET_NAME and AWS_REGION env vars

### Error: "AccessDeniedException"
- IAM role lacks permission
- Verify IAM policy is attached to correct role
- Check secret ARN in policy matches actual secret

### Error: Falls back to environment variables
- `USE_SECRETS_MANAGER` is not set to "true"
- Secrets Manager initialization failed
- Check application logs for errors

## Cost Estimation

AWS Secrets Manager pricing (as of 2024):
- $0.40 per secret per month
- $0.05 per 10,000 API calls

For this application:
- 1 secret: $0.40/month
- ~10,000 API calls/month: $0.05
- **Total: ~$0.45/month**

## Migration Checklist

- [ ] Create IAM policy for Secrets Manager access
- [ ] Create secret in AWS Secrets Manager with all credentials
- [ ] Attach IAM policy to application execution role
- [ ] Update production environment: `USE_SECRETS_MANAGER=true`
- [ ] Deploy updated application
- [ ] Verify secrets are loaded correctly
- [ ] Delete `.env` file from production server
- [ ] Document rotation schedule
- [ ] Set up CloudWatch alerts for secret access
- [ ] Schedule first manual rotation in 30 days

## Support

If you encounter issues:
1. Check CloudWatch logs for application errors
2. Verify IAM permissions with AWS IAM Policy Simulator
3. Test secret retrieval with AWS CLI:
   ```bash
   aws secretsmanager get-secret-value --secret-id slackbot/production
   ```
