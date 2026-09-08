from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Boolean, Text, DateTime

from db.db import Base   


class ChannelAccount(Base):
    
    __tablename__ = "channel_accounts"

    id = Column(Integer, primary_key=True)
    label = Column(String(100), nullable=False)
    channel_type = Column(String(20), nullable=False, index=True)
    identifier = Column(String(255), nullable=False) 
    credentials_enc = Column(Text, nullable=False)
    is_verified = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
                        onupdate=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
