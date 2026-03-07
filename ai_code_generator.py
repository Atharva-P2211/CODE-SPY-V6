"""
AWS Lambda Function: CodeSPY AI Code Generator
Service: Amazon Bedrock (Claude 3 Haiku)
Endpoint: POST /ai-code
Purpose: Generate fresh unique AI solutions per challenge per round
"""

import json
import boto3
import random

bedrock = boto3.client('bedrock-runtime', region_name='ap-south-1')

# Personality prompts per difficulty — makes AI feel different each game
PERSONALITIES = {
    'rookie': [
        "You are a student who just learned programming. Write simple, slightly imperfect answers. Sometimes miss edge cases. Don't over-explain.",
        "You are a non-programmer trying to solve a logic puzzle. Write naturally, make small mistakes sometimes.",
    ],
    'apprentice': [
        "You are a junior developer. Write working code but keep it simple. Avoid type hints. Use basic variable names like 'i', 'res', 'ans'.",
        "You are a self-taught coder. Code works but style is inconsistent. Mix camelCase and snake_case occasionally.",
    ],
    'agent': [
        "You are a mid-level software engineer. Write clean Python. Use type hints sometimes but not always. Add one brief comment.",
        "You are a developer in a hurry. Write correct but slightly rushed code. Skip docstrings. Use concise variable names.",
        "You are a careful programmer. Write well-structured code with a brief docstring. Use type hints. Be methodical.",
    ],
    'elite': [
        "You are a senior engineer. Write optimal, clean Python with full type hints and a proper docstring including time complexity.",
        "You are a competitive programmer. Write extremely concise, clever code. One-liners where possible. Minimal comments.",
        "You are a tech lead doing a code review. Write textbook-perfect code with Big-O analysis in comments.",
    ]
}

# Imperfection injectors — makes AI less obvious
def inject_imperfection(code, tier):
    """Randomly apply subtle human-like imperfections based on tier."""
    imperfection_chance = {'rookie': 0.7, 'apprentice': 0.5, 'agent': 0.25, 'elite': 0.1}
    chance = imperfection_chance.get(tier, 0.25)

    if random.random() > chance:
        return code  # No imperfection this time

    imperfections = [
        lambda c: c.replace('    ', '  ', random.randint(1, 2)),  # 2-space indent
        lambda c: c + '\n    # TODO: handle edge cases',
        lambda c: c.replace('def ', 'def  ', 1),  # Extra space (typo)
        lambda c: c.replace('return', 'return  ', 1),  # Extra space
        lambda c: c.replace(':int', ''),  # Remove some type hints
        lambda c: c.replace(': str', ''),
        lambda c: c.replace('"""', '#', 1).replace('"""', '', 1),  # Wrong docstring
    ]

    chosen = random.choice(imperfections)
    try:
        return chosen(code)
    except:
        return code


def lambda_handler(event, context):
    """
    POST /ai-code
    Body: { challengeId, challengeTitle, challengeDesc, task, tier }
    Returns: { code: string }
    """
    try:
        body = json.loads(event.get('body', '{}'))
        challenge_id    = body.get('challengeId', 'unknown')
        challenge_title = body.get('challengeTitle', 'Unknown Challenge')
        challenge_desc  = body.get('challengeDesc', '')
        task            = body.get('task', '')
        tier            = body.get('tier', 'agent')
        example_input   = body.get('exampleInput', '')
        example_output  = body.get('exampleOutput', '')

        # Pick a random personality for this tier
        personalities = PERSONALITIES.get(tier, PERSONALITIES['agent'])
        personality = random.choice(personalities)

        # Language hint based on tier
        lang_hint = 'plain text (no code needed)' if tier == 'rookie' else 'Python'

        # Build the prompt
        prompt = f"""{personality}

Solve this coding challenge in {lang_hint}:

Title: {challenge_title}
Description: {challenge_desc}
Task: {task}
Example Input: {example_input}
Example Output: {example_output}

Rules:
- Write ONLY the solution code or answer, nothing else
- No explanation before or after
- No markdown code fences
- For Python: write a complete function
- Keep it under 25 lines
- Make it look like something a real person typed under time pressure"""

        # Call Bedrock - Claude 3 Haiku (fastest, cheapest)
        response = bedrock.invoke_model(
            modelId='anthropic.claude-3-haiku-20240307-v1:0',
            body=json.dumps({
                'anthropic_version': 'bedrock-2023-05-31',
                'max_tokens': 512,
                'messages': [{'role': 'user', 'content': prompt}]
            })
        )

        result = json.loads(response['body'].read())
        code = result['content'][0]['text'].strip()

        # Strip any accidental markdown fences
        if code.startswith('```'):
            lines = code.split('\n')
            code = '\n'.join(lines[1:-1] if lines[-1] == '```' else lines[1:])

        # Inject imperfection based on tier
        code = inject_imperfection(code, tier)

        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': 'Content-Type',
            },
            'body': json.dumps({
                'code': code,
                'tier': tier,
                'model': 'claude-3-haiku',
                'challengeId': challenge_id
            })
        }

    except Exception as e:
        print(f"Error: {str(e)}")
        # Fallback — return a pre-written solution so game never breaks
        return {
            'statusCode': 200,
            'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
            'body': json.dumps({
                'code': '# AI solution\ndef solution():\n    pass',
                'error': str(e),
                'fallback': True
            })
        }
