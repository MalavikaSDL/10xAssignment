This is a backend system for an autonomous wall-finishing robot. It uses:
1. A* path planning algorithm
2. FastAPI for the RESTful API
3. PostgreSQL to store wall and path data
4. RabbitMQ to send path instructions to the robot
5. Redis to cache obstacle and path data
