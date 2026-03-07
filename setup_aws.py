#!/usr/bin/env python3
"""
CodeSPY AWS Setup Script
Run this ONCE after getting your AWS credentials.
Creates: DynamoDB tables, Lambda functions, API Gateway, IAM roles.
Usage: python3 setup_aws.py
"""

import boto3
import json
import zipfile
import os
import time
import sys

# ── CONFIG — Fill these in ──
AWS_REGION      = 'ap-south-1'   # Mumbai
AWS_ACCOUNT_ID  = ''             # Your 12-digit account ID (find in top-right of console)
# ────────────────────────────

print('\n🚀 CodeSPY AWS Setup Starting...\n')

session  = boto3.session.Session(region_name=AWS_REGION)
iam      = session.client('iam')
dynamodb = session.client('dynamodb')
lam      = session.client('lambda')
apigw    = session.client('apigateway')

# ── STEP 1: Create DynamoDB Tables ──────────────────────────
print('📊 Creating DynamoDB tables...')

def create_table_safe(name, key_schema, attr_defs):
    try:
        dynamodb.create_table(
            TableName=name,
            KeySchema=key_schema,
            AttributeDefinitions=attr_defs,
            BillingMode='PAY_PER_REQUEST',
            TimeToLiveSpecification={'Enabled': True, 'AttributeName': 'ttl'}
            if name == 'codespy-leaderboard' else {}
        )
        print(f'  ✓ Created table: {name}')
    except dynamodb.exceptions.ResourceInUseException:
        print(f'  ⚠ Table already exists: {name}')

create_table_safe(
    'codespy-leaderboard',
    [{'AttributeName': 'recordId', 'KeyType': 'HASH'}],
    [{'AttributeName': 'recordId', 'AttributeType': 'S'}]
)

create_table_safe(
    'codespy-stats',
    [{'AttributeName': 'statId', 'KeyType': 'HASH'}],
    [{'AttributeName': 'statId', 'AttributeType': 'S'}]
)

# Initialize stats record
try:
    dynamodb_res = session.resource('dynamodb')
    stats_table  = dynamodb_res.Table('codespy-stats')
    time.sleep(3)  # Wait for table to be ready
    stats_table.put_item(Item={
        'statId':       'global',
        'totalGames':   0,
        'totalCatches': 0,
        'totalPlayers': 0,
        'lastUpdated':  int(time.time() * 1000)
    })
    print('  ✓ Initialized stats record')
except Exception as e:
    print(f'  ⚠ Stats init (will retry): {e}')

print()

# ── STEP 2: Create IAM Role for Lambda ──────────────────────
print('🔐 Creating IAM role for Lambda...')

trust_policy = json.dumps({
    'Version': '2012-10-17',
    'Statement': [{
        'Effect': 'Allow',
        'Principal': {'Service': 'lambda.amazonaws.com'},
        'Action': 'sts:AssumeRole'
    }]
})

try:
    role = iam.create_role(
        RoleName='codespy-lambda-role',
        AssumeRolePolicyDocument=trust_policy,
        Description='CodeSPY Lambda execution role'
    )
    role_arn = role['Role']['Arn']
    print(f'  ✓ Created role: codespy-lambda-role')
except iam.exceptions.EntityAlreadyExistsException:
    role_arn = iam.get_role(RoleName='codespy-lambda-role')['Role']['Arn']
    print(f'  ⚠ Role already exists')

# Attach policies
policies = [
    'arn:aws:iam::aws:policy/AmazonDynamoDBFullAccess',
    'arn:aws:iam::aws:policy/AmazonBedrockFullAccess',
    'arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole',
]
for policy in policies:
    try:
        iam.attach_role_policy(RoleName='codespy-lambda-role', PolicyArn=policy)
        print(f'  ✓ Attached: {policy.split("/")[-1]}')
    except Exception as e:
        print(f'  ⚠ Policy attach: {e}')

print('  ⏳ Waiting 10s for IAM role to propagate...')
time.sleep(10)
print()

# ── STEP 3: Package + Deploy Lambda Functions ───────────────
print('⚡ Deploying Lambda functions...')

def zip_lambda(source_file, zip_path):
    with zipfile.ZipFile(zip_path, 'w') as z:
        z.write(source_file, 'lambda_function.py')

def deploy_lambda(name, source_file, description, timeout=30):
    zip_path = f'/tmp/{name}.zip'
    zip_lambda(source_file, zip_path)
    with open(zip_path, 'rb') as f:
        zip_bytes = f.read()

    try:
        func = lam.create_function(
            FunctionName=name,
            Runtime='python3.12',
            Role=role_arn,
            Handler='lambda_function.lambda_handler',
            Code={'ZipFile': zip_bytes},
            Description=description,
            Timeout=timeout,
            MemorySize=256,
            Environment={'Variables': {'REGION': AWS_REGION}}
        )
        print(f'  ✓ Created Lambda: {name}')
        return func['FunctionArn']
    except lam.exceptions.ResourceConflictException:
        func = lam.update_function_code(
            FunctionName=name,
            ZipFile=zip_bytes
        )
        print(f'  ⚠ Updated existing Lambda: {name}')
        return func['FunctionArn']

ai_arn = deploy_lambda(
    'codespy-ai-generator',
    '/home/claude/codespy-aws/lambda/ai_code_generator.py',
    'CodeSPY: Generate AI code via Bedrock',
    timeout=30
)

lb_arn = deploy_lambda(
    'codespy-leaderboard',
    '/home/claude/codespy-aws/lambda/leaderboard.py',
    'CodeSPY: Global leaderboard via DynamoDB',
    timeout=15
)

print()

# ── STEP 4: Create API Gateway ──────────────────────────────
print('🌐 Creating API Gateway...')

api = apigw.create_rest_api(
    name='codespy-api',
    description='CodeSPY REST API',
    endpointConfiguration={'types': ['REGIONAL']}
)
api_id = api['id']
print(f'  ✓ Created API: {api_id}')

root_id = apigw.get_resources(restApiId=api_id)['items'][0]['id']

def create_endpoint(path_part, lambda_arn, methods=['GET', 'POST', 'OPTIONS']):
    # Create resource
    resource = apigw.create_resource(
        restApiId=api_id,
        parentId=root_id,
        pathPart=path_part
    )
    resource_id = resource['id']

    for method in methods:
        # Create method
        apigw.put_method(
            restApiId=api_id,
            resourceId=resource_id,
            httpMethod=method,
            authorizationType='NONE'
        )

        if method == 'OPTIONS':
            # CORS mock integration
            apigw.put_integration(
                restApiId=api_id,
                resourceId=resource_id,
                httpMethod='OPTIONS',
                type='MOCK',
                requestTemplates={'application/json': '{"statusCode": 200}'}
            )
            apigw.put_method_response(
                restApiId=api_id, resourceId=resource_id,
                httpMethod='OPTIONS', statusCode='200',
                responseParameters={
                    'method.response.header.Access-Control-Allow-Headers': False,
                    'method.response.header.Access-Control-Allow-Methods': False,
                    'method.response.header.Access-Control-Allow-Origin': False,
                }
            )
            apigw.put_integration_response(
                restApiId=api_id, resourceId=resource_id,
                httpMethod='OPTIONS', statusCode='200',
                responseParameters={
                    'method.response.header.Access-Control-Allow-Headers': "'Content-Type'",
                    'method.response.header.Access-Control-Allow-Methods': "'GET,POST,OPTIONS'",
                    'method.response.header.Access-Control-Allow-Origin': "'*'",
                }
            )
        else:
            # Lambda proxy integration
            region = AWS_REGION
            uri = f'arn:aws:apigateway:{region}:lambda:path/2015-03-31/functions/{lambda_arn}/invocations'
            apigw.put_integration(
                restApiId=api_id,
                resourceId=resource_id,
                httpMethod=method,
                type='AWS_PROXY',
                integrationHttpMethod='POST',
                uri=uri
            )
            # Add Lambda permission
            try:
                lam.add_permission(
                    FunctionName=lambda_arn,
                    StatementId=f'apigw-{path_part}-{method}',
                    Action='lambda:InvokeFunction',
                    Principal='apigateway.amazonaws.com',
                    SourceArn=f'arn:aws:execute-api:{region}:{AWS_ACCOUNT_ID}:{api_id}/*/*/{path_part}'
                )
            except Exception:
                pass

    print(f'  ✓ Created endpoint: /{path_part}')
    return resource_id

create_endpoint('ai-code',     ai_arn)
create_endpoint('leaderboard', lb_arn)
create_endpoint('stats',       lb_arn)

# Deploy to prod stage
apigw.create_deployment(restApiId=api_id, stageName='prod')

api_url = f'https://{api_id}.execute-api.{AWS_REGION}.amazonaws.com/prod'
print(f'\n  ✅ API deployed at: {api_url}')
print()

# ── STEP 5: Save config for game ────────────────────────────
config = {
    'apiUrl':    api_url,
    'region':    AWS_REGION,
    'aiEndpoint':          f'{api_url}/ai-code',
    'leaderboardEndpoint': f'{api_url}/leaderboard',
    'statsEndpoint':       f'{api_url}/stats',
}

with open('/home/claude/codespy-aws/aws-config.json', 'w') as f:
    json.dump(config, f, indent=2)

print('📝 Config saved to aws-config.json')
print()
print('═' * 50)
print('✅ AWS SETUP COMPLETE!')
print('═' * 50)
print(f'\n  API URL: {api_url}')
print(f'\n  Next: run python3 build_v6.py to bundle the game')
print()
