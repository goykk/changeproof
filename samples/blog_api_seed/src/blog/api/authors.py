"""作者路由：另一个路由文件，用于验证多路由文件的索引与影响传播。"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Author

router = APIRouter(prefix="/authors", tags=["authors"])


@router.get("")
def list_authors(db: Session = Depends(get_db)):
    return [{"id": author.id, "name": author.name} for author in db.query(Author).all()]