#!/bin/bash
# Script to convert GitHub services from sync to async

echo "Converting GitHub services to async..."

# Convert github_oauth_service.py
sed -i.bak '
s/def store_connection(/async def store_connection(/g
s/def get_user_connection(/async def get_user_connection(/g
s/def disconnect_user(/async def disconnect_user(/g
s/def get_or_create_integration(/async def get_or_create_integration(/g
s/db\.query(/await db.execute(select(/g
s/\.first()/)).scalar_one_or_none()/g
s/db\.commit()/await db.commit()/g
s/db\.rollback()/await db.rollback()/g
s/db\.refresh(/await db.refresh(/g
s/db\.add(/db.add(/g
s/db\.delete(/await db.delete(/g
s/from sqlalchemy.orm import Session/from sqlalchemy.ext.asyncio import AsyncSession/g
s/db: Session/db: AsyncSession/g
' app/services/github_services/github_oauth_service.py

echo "Conversion complete. Check .bak files for originals."
echo "Note: Manual review needed for complex query conversions."
