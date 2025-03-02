from flask import Flask, render_template, request, jsonify, session
from flask_session import Session
from google import genai
import re
import os
from dotenv import load_dotenv
from pymongo import MongoClient

# 2. Configure MongoDB connection
load_dotenv()
MONGODB_URI = os.environ.get("MONGODB_URI", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

mongo_client = MongoClient(MONGODB_URI)
db = mongo_client["sample_database"]    # Database name
collection = db["pgagi"]               # Collection name

app = Flask(__name__)
app.secret_key = 'some_random_secret_key'
app.config['SESSION_TYPE'] = 'filesystem'
Session(app)

client = genai.Client(api_key=GEMINI_API_KEY)

def validate_with_gemini(user_input, field_type):
    """
    Validate user input for a specific field type using Gemini.
    Must respond STRICTLY with "VALID" or "INVALID".
    """
    prompt = f"""
You are a strict validator for user input fields.

### Field Type:
{field_type}

### User Input:
{user_input}

### Rules:
- Respond ONLY with "VALID" or "INVALID" (uppercase, no extra words).
- Full Name: At least two words, primarily alphabetic.
- Email Address: Must have '@' and a domain extension like .com, etc.
- Phone Number: Mostly digits (+, -, spaces), at least 7 digits.
- Years of Experience: Integer 0-60.
- Desired Position: At least 2 letters.
- Current Location: At least 2 letters.
- Tech Stack: Non-empty, at least 2 letters.
    """.strip()

    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt
        )
        result = response.text.strip().upper()
        return (result == "VALID")
    except Exception as e:
        print("Gemini validation error:", e)
        return False

@app.route('/')
def index():
    session.clear()
    return render_template('index.html')

@app.route('/chat', methods=['POST'])
def chat():
    if 'stage' not in session:
        session['stage'] = 0
        session['candidate_data'] = {}

    user_message = request.json.get('message', '').strip()
    stage = session['stage']
    candidate_data = session['candidate_data']

    # End conversation keywords
    if user_message.lower() in ['exit', 'quit', 'bye']:
        session.clear()
        return jsonify({'bot_message': "Thank you for using TalentScout Hiring Assistant. Goodbye!"})

    # Conversation flow
    if stage == 0:
        # Stage 0: greet
        session['stage'] = 1
        return jsonify({
            'bot_message': (
                "Hello! Welcome to TalentScout's Hiring Assistant chatbot. "
                "I'm here to help with the initial screening process. "
                "Let's begin.\n\nWhat is your full name?"
            )
        })

    elif stage == 1:
        # Full name
        if validate_with_gemini(user_message, "full name"):
            candidate_data['full_name'] = user_message
            session['stage'] = 2
            return jsonify({'bot_message': "Great! What's your email address?"})
        else:
            return jsonify({'bot_message': "That doesn't look like a valid full name. Please try again."})

    elif stage == 2:
        # Email
        sanitized_email = re.sub(r'[<>]', '', user_message.strip())
        email_regex = r'^[\w\.-]+@[\w\.-]+\.\w+$'
        if re.match(email_regex, sanitized_email):
            if validate_with_gemini(sanitized_email, "email address"):
                candidate_data['email'] = sanitized_email
                session['stage'] = 3
                return jsonify({'bot_message': "Thanks! What's your phone number?"})
            else:
                return jsonify({'bot_message': "That doesn't look like a valid email address. Please try again."})
        else:
            return jsonify({'bot_message': "That doesn't look like a valid email address. Please try again."})

    elif stage == 3:
        # Phone
        if validate_with_gemini(user_message, "phone number"):
            candidate_data['phone'] = user_message
            session['stage'] = 4
            return jsonify({'bot_message': "Got it. How many years of experience do you have?"})
        else:
            return jsonify({'bot_message': "That doesn't look like a valid phone number. Please try again."})

    elif stage == 4:
        # Years of experience (0-50)
        try:
            years = int(user_message)
            if 0 <= years <= 50:
                if validate_with_gemini(user_message, "years of experience"):
                    candidate_data['years_exp'] = user_message
                    session['stage'] = 5
                    return jsonify({'bot_message': "Understood. What is your desired position(s)?"})
                else:
                    return jsonify({'bot_message': "That doesn't look like a valid response for years of experience. Please try again."})
            else:
                return jsonify({'bot_message': "Years of experience must be between 0 and 50. Please try again."})
        except ValueError:
            return jsonify({'bot_message': "That doesn't look like a valid integer. Please try again."})

    elif stage == 5:
        # Desired position
        if validate_with_gemini(user_message, "desired position"):
            candidate_data['desired_positions'] = user_message
            session['stage'] = 6
            return jsonify({'bot_message': "Thank you. What's your current location?"})
        else:
            return jsonify({'bot_message': "That doesn't look like a valid desired position. Please try again."})

    elif stage == 6:
        # Current location
        if validate_with_gemini(user_message, "current location"):
            candidate_data['current_location'] = user_message
            session['stage'] = 7
            return jsonify({'bot_message': "Great! Please list your tech stack (e.g., Python, Django, SQL)."})
        else:
            return jsonify({'bot_message': "That doesn't look like a valid location. Please try again."})

    elif stage == 7:
        if validate_with_gemini(user_message, "tech stack"):
            candidate_data['tech_stack'] = user_message

            prompt_text = (
                f"You are an interviewer creating beginner-level questions for the tech stack: {user_message}. "
                "Generate exactly 3 short questions, each limited to 2 lines. "
                "Avoid advanced or lengthy explanations—keep them simple and concise."
            )
            try:
                response = client.models.generate_content(
                    model="gemini-2.0-flash",
                    contents=prompt_text
                )
                questions_text = response.text.strip()

                # Split into lines, ignoring empty lines
                questions_list = [q.strip() for q in questions_text.split('\n') if q.strip()]

                # 1) If the first line is just an intro like "Here are three beginner-level..."
                #    remove it so the next lines are the actual questions.
                if questions_list and 'here are' in questions_list[0].lower():
                    questions_list.pop(0)

                session['tech_questions_list'] = questions_list
                session['answers'] = []
                session['current_q_index'] = 0

                # Move to stage 8 and ask the first question
                session['stage'] = 8
                if questions_list:
                    first_question = questions_list[0]
                    return jsonify({
                        'bot_message': (
                            "Thanks! Let's go through your tech questions.\n\n"
                            f"Question 1: {first_question}"
                        )
                    })
                else:
                    # If no questions, skip to final
                    session['stage'] = 11
                    return jsonify({'bot_message': "No questions generated. Type 'exit' to end."})

            except Exception as e:
                print("Gemini API error:", e)
                return jsonify({
                    'bot_message': "Oops, there was an error generating questions. Please try again or type 'exit' to end."
                })
        else:
            return jsonify({'bot_message': "That doesn't look like a valid tech stack. Please try again."})


    # --------------- Handling the 3 Tech Questions ---------------
    elif stage == 8:
        # The user is answering question 1
        answers = session.get('answers', [])
        answers.append(user_message)
        session['answers'] = answers

        questions_list = session.get('tech_questions_list', [])
        session['current_q_index'] = 1  # Next question
        session['stage'] = 9

        if len(questions_list) > 1:
            return jsonify({'bot_message': f"Question 2: {questions_list[1]}"})
        else:
            # If there's no second question, jump to final
            session['stage'] = 11
            return jsonify({'bot_message': "No more questions. Type 'exit' to end."})

    elif stage == 9:
        # The user is answering question 2
        answers = session.get('answers', [])
        answers.append(user_message)
        session['answers'] = answers

        questions_list = session.get('tech_questions_list', [])
        session['current_q_index'] = 2
        session['stage'] = 10

        if len(questions_list) > 2:
            return jsonify({'bot_message': f"Question 3: {questions_list[2]}"})
        else:
            # If there's no third question, jump to final
            session['stage'] = 11
            return jsonify({'bot_message': "No more questions. Type 'exit' to end."})

    elif stage == 10:
        # The user is answering question 3
        answers = session.get('answers', [])
        answers.append(user_message)
        session['answers'] = answers

        # Move to final stage
        session['stage'] = 11
        return jsonify({'bot_message': "Thanks for your answers! Type 'done' to finalize or 'exit' to quit."})

    elif stage == 11:
        # Wait for user to type "done" to finalize or "exit"
        if user_message.lower() == 'done':
            # Insert everything into MongoDB
            candidate_data['answers'] = session.get('answers', [])
            candidate_data['questions'] = session.get('tech_questions_list', [])

            # Insert doc
            collection.insert_one({
                "full_name": candidate_data.get('full_name'),
                "email": candidate_data.get('email'),
                "phone": candidate_data.get('phone'),
                "years_exp": candidate_data.get('years_exp'),
                "desired_positions": candidate_data.get('desired_positions'),
                "current_location": candidate_data.get('current_location'),
                "tech_stack": candidate_data.get('tech_stack'),
                "questions": candidate_data.get('questions'),
                "answers": candidate_data.get('answers')
            })

            # Clear session or move to stage 12
            session['stage'] = 12
            return jsonify({'bot_message': "All data saved! Thank you. Type 'exit' to leave or continue chatting."})
        else:
            return jsonify({'bot_message': "Type 'done' to finalize or 'exit' to quit."})

    else:
        # Stage >= 12
        return jsonify({
            'bot_message': "We've already saved your data. Type 'exit' to end."
        })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
