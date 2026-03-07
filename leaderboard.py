"""
AWS Lambda Function: CodeSPY Global Leaderboard
Service: Amazon DynamoDB
Endpoints:
  POST /leaderboard  — save a player's score
  GET  /leaderboard  — fetch top 10 global scores
  GET  /stats        — fetch global game stats
"""

import json
import boto3
import time
from boto3.dynamodb.conditions import Key
from decimal import Decimal

dynamodb = boto3.resource('dynamodb', region_name='ap-south-1')
table = dynamodb.Table('codespy-leaderboard')
stats_table = dynamodb.Table('codespy-stats')


class DecimalEncoder(json.JSONEncoder):
    """Handle DynamoDB Decimal type in JSON."""
    def default(self, obj):
        if isinstance(obj, Decimal):
            return int(obj)
        return super().default(obj)


def save_score(body):
    """Save a player's round score to DynamoDB."""
    player_name = body.get('playerName', 'Anonymous')[:20]
    score       = int(body.get('score', 0))
    tier        = body.get('tier', 'agent')
    caught_ai   = bool(body.get('caughtAI', False))
    streak      = int(body.get('streak', 0))
    timestamp   = int(time.time() * 1000)

    # Composite key: playerName#timestamp for uniqueness
    record_id = f"{player_name}#{timestamp}"

    table.put_item(Item={
        'recordId':   record_id,
        'playerName': player_name,
        'score':      score,
        'tier':       tier,
        'caughtAI':   caught_ai,
        'streak':     streak,
        'timestamp':  timestamp,
        # TTL: keep records for 90 days
        'ttl':        int(time.time()) + (90 * 24 * 60 * 60)
    })

    # Update global stats
    try:
        stats_table.update_item(
            Key={'statId': 'global'},
            UpdateExpression='''
                ADD totalGames :one,
                    totalCatches :catch,
                    totalPlayers :one
                SET lastUpdated = :ts
            ''',
            ExpressionAttributeValues={
                ':one':   1,
                ':catch': 1 if caught_ai else 0,
                ':ts':    timestamp
            }
        )
    except Exception as e:
        print(f"Stats update error: {e}")

    return {'saved': True, 'recordId': record_id}


def get_leaderboard():
    """Fetch top 10 scores globally using a scan + sort."""
    # In production use a GSI on score for efficiency
    # For hackathon, scan is fine with small data
    response = table.scan(
        ProjectionExpression='playerName, score, tier, caughtAI, streak, #ts',
        ExpressionAttributeNames={'#ts': 'timestamp'},
        Limit=200  # scan recent 200 records
    )

    items = response.get('Items', [])

    # Sort by score descending, take top 10
    sorted_items = sorted(items, key=lambda x: int(x.get('score', 0)), reverse=True)
    top10 = sorted_items[:10]

    # Clean up for JSON
    leaderboard = []
    medals = ['🥇', '🥈', '🥉']
    for i, item in enumerate(top10):
        leaderboard.append({
            'rank':       i + 1,
            'medal':      medals[i] if i < 3 else f'#{i+1}',
            'playerName': item.get('playerName', 'Unknown'),
            'score':      int(item.get('score', 0)),
            'tier':       item.get('tier', 'agent'),
            'caughtAI':   bool(item.get('caughtAI', False)),
            'streak':     int(item.get('streak', 0)),
        })

    return leaderboard


def get_stats():
    """Fetch global game statistics."""
    try:
        response = stats_table.get_item(Key={'statId': 'global'})
        item = response.get('Item', {})
        total_games   = int(item.get('totalGames', 0))
        total_catches = int(item.get('totalCatches', 0))
        catch_rate    = round((total_catches / total_games * 100) if total_games > 0 else 0)
        return {
            'totalGames':  total_games,
            'totalCatches': total_catches,
            'catchRate':   catch_rate,
            'totalPlayers': int(item.get('totalPlayers', 0)),
        }
    except Exception as e:
        print(f"Stats fetch error: {e}")
        return {'totalGames': 0, 'catchRate': 0, 'totalPlayers': 0}


def lambda_handler(event, context):
    method = event.get('httpMethod', 'GET')
    path   = event.get('path', '/')

    headers = {
        'Content-Type': 'application/json',
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Headers': 'Content-Type',
        'Access-Control-Allow-Methods': 'GET,POST,OPTIONS'
    }

    # Handle CORS preflight
    if method == 'OPTIONS':
        return {'statusCode': 200, 'headers': headers, 'body': ''}

    try:
        if method == 'POST' and '/leaderboard' in path:
            body = json.loads(event.get('body', '{}'))
            result = save_score(body)
            return {
                'statusCode': 200,
                'headers': headers,
                'body': json.dumps(result, cls=DecimalEncoder)
            }

        elif method == 'GET' and '/leaderboard' in path:
            leaderboard = get_leaderboard()
            return {
                'statusCode': 200,
                'headers': headers,
                'body': json.dumps({'leaderboard': leaderboard}, cls=DecimalEncoder)
            }

        elif method == 'GET' and '/stats' in path:
            stats = get_stats()
            return {
                'statusCode': 200,
                'headers': headers,
                'body': json.dumps(stats, cls=DecimalEncoder)
            }

        else:
            return {
                'statusCode': 404,
                'headers': headers,
                'body': json.dumps({'error': 'Route not found'})
            }

    except Exception as e:
        print(f"Handler error: {str(e)}")
        return {
            'statusCode': 500,
            'headers': headers,
            'body': json.dumps({'error': 'Internal server error'})
        }
