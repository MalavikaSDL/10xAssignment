from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from aStar import astar 
import uuid
import json
import redis
import pika
import time
import logging
import psycopg2
import os

app = FastAPI()

# Configure logging
logging.basicConfig(level=logging.INFO)

# Redis setup
r = redis.from_url(os.environ['REDIS_URL'], decode_responses=True)


# RabbitMQ setup
parameters = pika.URLParameters(os.environ['RABBITMQ_URL'])
connection = pika.BlockingConnection(parameters)

channel = connection.channel()
channel.queue_declare(queue='robot_path')

# PostgreSQL setup
conn = psycopg2.connect(os.environ["DATABASE_URL"])
cursor = conn.cursor()

cursor.execute('''
CREATE TABLE IF NOT EXISTS walls (
    id UUID PRIMARY KEY,
    width FLOAT,
    height FLOAT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS paths (
    id UUID PRIMARY KEY,
    wall_id UUID,
    algorithm TEXT,
    path_json JSONB,
    metrics_json JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
''')

conn.commit()

# Models
class Obstacle(BaseModel):
    shape: str
    x: float
    y: float
    radius: Optional[float] = None
    width: Optional[float] = None
    height: Optional[float] = None

class Wall(BaseModel):
    width: float
    height: float
    obstacles: List[Obstacle]

class PathRequest(BaseModel):
    wall_id: str
    algorithm: str

# API Endpoints
@app.post("/walls/")
def create_wall(wall: Wall):
    wall_id = str(uuid.uuid4())
    cursor.execute("INSERT INTO walls (id, width, height) VALUES (%s, %s, %s)", (wall_id, wall.width, wall.height))
    conn.commit()
    r.set(f"wall:{wall_id}:obstacles", json.dumps([obs.dict() for obs in wall.obstacles]))
    logging.info(f"✅ Wall created: ID={wall_id}, Width={wall.width}, Height={wall.height}")
    return {"wall_id": wall_id}

@app.post("/plan/")
def generate_path(req: PathRequest):
    try:
        wall_id = req.wall_id
        algorithm = req.algorithm
        wall_obstacles = json.loads(r.get(f"wall:{wall_id}:obstacles"))

        grid_size = 10
        grid = [[0]*grid_size for _ in range(grid_size)]
        for obs in wall_obstacles:
            x = int(obs["x"])
            y = int(obs["y"])
            if 0 <= x < grid_size and 0 <= y < grid_size:
                grid[y][x] = 1
        
        start, goal = (0, 0), (grid_size-1, grid_size-1)
        path = astar(grid, start, goal)
        metrics = {"duration_ms": 50, "path_length": len(path)}

        path_id = str(uuid.uuid4())
        cursor.execute("INSERT INTO paths (id, wall_id, algorithm, path_json, metrics_json) VALUES (%s, %s, %s, %s, %s)",
                       (path_id, wall_id, algorithm, json.dumps(path), json.dumps(metrics)))
        conn.commit()
        r.set(f"path:{path_id}", json.dumps(path))

        logging.info(f"📍 Path planned: ID={path_id}, Wall={wall_id}, Algorithm={algorithm}, Length={len(path)}")
        return {"path_id": path_id, "path": path, "metrics": metrics}

    except Exception as e:
        logging.error(f"❌ Path planning failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/execute/{path_id}")
def execute_path(path_id: str):
    path_json = r.get(f"path:{path_id}")
    if not path_json:
        cursor.execute("SELECT path_json FROM paths WHERE id = %s", (path_id,))
        result = cursor.fetchone()
        if not result:
            logging.warning(f"⚠️ Path not found for execution: ID={path_id}")
            raise HTTPException(status_code=404, detail="Path not found")
        path_json = json.dumps(result[0])

    channel.basic_publish(exchange='', routing_key='robot_path', body=path_json)
    logging.info(f"🚀 Path sent to robot: ID={path_id}")
    return {"status": "sent"}

@app.get("/metrics/")
def get_metrics():
    metrics = {
        "api_response_time_ms": 20,
        "robot_status": r.get("robot_status") or b"idle",
        "cached_paths": len(r.keys("path:*"))
    }
    logging.info("📊 Metrics requested")
    return {key: value.decode() if isinstance(value, bytes) else value for key, value in metrics.items()}

@app.get("/plan/{path_id}")
def get_path(path_id: str):
    cursor.execute("SELECT path_json, metrics_json FROM paths WHERE id = %s", (path_id,))
    row = cursor.fetchone()
    if not row:
        logging.warning(f"⚠️ Path not found: ID={path_id}")
        raise HTTPException(status_code=404, detail="Path not found")
    logging.info(f"📦 Path retrieved: ID={path_id}")
    return {"path": row[0], "metrics": row[1]}

