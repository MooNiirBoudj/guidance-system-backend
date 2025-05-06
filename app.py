from flask import Flask, request, jsonify
from flask_cors import CORS
import pickle
import numpy as np
import pandas as pd
import google.generativeai as genai
from dotenv import load_dotenv
from sklearn.preprocessing import OrdinalEncoder, OneHotEncoder
import os
from itertools import combinations
import traceback  # Added for better error logging

# Load environment variables
load_dotenv()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
if not GOOGLE_API_KEY:
    print("WARNING: GOOGLE_API_KEY environment variable not found!")

# Configure Gemini
try:
    genai.configure(api_key=GOOGLE_API_KEY)
    gemini_model = genai.GenerativeModel('gemini-1.5-flash')
    print("Gemini model initialized successfully")
except Exception as e:
    print(f"Error initializing Gemini model: {str(e)}")
    # Set a flag to disable Gemini functionality if it fails to initialize
    gemini_available = False
else:
    gemini_available = True

# Load Random Forest model and other files with better error handling
try:
    with open('best_model.pkl', 'rb') as file:
        rf_model = pickle.load(file)
    print("Random Forest model loaded successfully")
except Exception as e:
    print(f"Error loading Random Forest model: {str(e)}")
    rf_model = None

try:
    with open("ordinal_encoder.pkl", "rb") as f:
        ordinal_encoder = pickle.load(f)
    print("Ordinal encoder loaded successfully")
except Exception as e:
    print(f"Error loading ordinal encoder: {str(e)}")
    ordinal_encoder = None

try:
    with open("onehot_encoder.pkl", "rb") as f:
        onehot_encoder = pickle.load(f)
    print("One-hot encoder loaded successfully")
except Exception as e:
    print(f"Error loading one-hot encoder: {str(e)}")
    onehot_encoder = None

try:
    with open("encoding_dicts.pkl", "rb") as f:
        encoding_dicts = pickle.load(f)
    print("Encoding dictionaries loaded successfully")
except Exception as e:
    print(f"Error loading encoding dictionaries: {str(e)}")
    encoding_dicts = None

# Feature grouping parameters
min_occurs = 4
degrees = [2, 3]

# Function for feature grouping
def dict_decode(encoding, value, min_occurs):
    enc = encoding.get(value, {'code': -1, 'count': 0})
    return enc['code'] if enc['count'] >= min_occurs else -1

# Load the selected feature indices
selected_feature_indices = [0, 82, 110, 122, 421, 451, 483, 489, 514, 530, 673, 798]
print(f"Selected feature indices: {selected_feature_indices}")
print(f"Max selected feature index: {max(selected_feature_indices)}")

# Try to load feature column names if available
try:
    with open("feature_columns.pkl", "rb") as f:
        selected_columns = pickle.load(f)
    use_column_names = True
    print(f"Feature columns loaded successfully: {len(selected_columns)} columns")
except FileNotFoundError:
    use_column_names = False
    print("Feature columns file not found, will use indices instead")
except Exception as e:
    use_column_names = False
    print(f"Error loading feature columns: {str(e)}")

# Initialize Flask app
app = Flask(__name__)
CORS(app)  # This enables CORS for all routes
app.config['PROPAGATE_EXCEPTIONS'] = True  # Add this to see detailed errors

# Global state for Gemini chat
chat_session = None
current_field = None
assessment_in_progress = False
question_count = 0
absolute_max_questions = 30  # Safety limit to prevent infinite questioning

# -------------------- Home --------------------
@app.route('/')
def index():
    return "Flask App: Random Forest API & Gemini Career Assessment"

# -------------------- Normalisation function --------------------
def normalize_text(text):
    if not isinstance(text, str):
        return text
    return text.replace("'", "'").replace(""", "\"").replace(""", "\"")

# -------------------- Random Forest Route --------------------
@app.route('/predict', methods=['POST'])
def predict():
    try:
        print("\n--- New prediction request received ---")
        
        # Check if model and encoders are loaded
        if rf_model is None or ordinal_encoder is None or onehot_encoder is None or encoding_dicts is None:
            return jsonify({"error": "Model or encoders not loaded. Check server logs."}), 500
        
        # Parse request data
        input_data = request.get_json()
        print(f"Request data received: {input_data.keys() if input_data else 'None'}")
        
        if not input_data or 'answers' not in input_data:
            return jsonify({"error": "Invalid request format. 'answers' key is required."}), 400
        
        answers = input_data['answers']
        print(f"Answers keys: {answers.keys() if answers else 'None'}")
        
        # Create DataFrame
        try:
            data = pd.DataFrame([answers])
            print(f"Created DataFrame with shape: {data.shape}")
        except Exception as e:
            print(f"Error creating DataFrame: {str(e)}")
            return jsonify({"error": f"Error creating DataFrame: {str(e)}"}), 400

        # Clean up any curly quotes and strip whitespace
        try:
            data = data.applymap(lambda x: normalize_text(x) if isinstance(x, str) else x)
            print("Normalized text in DataFrame")
        except Exception as e:
            print(f"Error normalizing text: {str(e)}")
            return jsonify({"error": f"Error normalizing text: {str(e)}"}), 400

        # Ordinal columns
        ordinal_columns = [
            "Do you enjoy and feel comfortable with subjects like mathematics, physics, and biology?",
            "Are you excited by combining theoretical learning with hands-on practical work?",
            "How do you handle long study hours and challenging academic content?",
            "How comfortable are you navigating sensitive or emotional situations?",
            "How do you feel about public speaking or presenting?"
        ]
        
        # Check if all ordinal columns are present
        missing_columns = [col for col in ordinal_columns if col not in data.columns]
        if missing_columns:
            print(f"Missing required columns: {missing_columns}")
            return jsonify({"error": f"Missing required columns: {missing_columns}"}), 400

        # Transform using preloaded encoders
        try:
            print("Applying ordinal encoding")
            data[ordinal_columns] = ordinal_encoder.transform(data[ordinal_columns])
            print("Ordinal encoding complete")
            
            categorical_columns = [col for col in data.columns if col not in ordinal_columns]
            print(f"Categorical columns: {len(categorical_columns)}")
            
            print("Applying one-hot encoding")
            onehot_encoded = onehot_encoder.transform(data[categorical_columns])
            print(f"One-hot encoded shape: {onehot_encoded.shape}")
            
            onehot_df = pd.DataFrame(
                onehot_encoded, 
                columns=onehot_encoder.get_feature_names_out(categorical_columns)
            )
            print(f"One-hot DataFrame shape: {onehot_df.shape}")
        except Exception as e:
            print(f"Error in encoding: {str(e)}")
            print(traceback.format_exc())
            return jsonify({"error": f"Error in encoding: {str(e)}"}), 400

        # Combine preprocessed data
        try:
            preprocessed_df = pd.concat([
                data[ordinal_columns].reset_index(drop=True), 
                onehot_df.reset_index(drop=True)
            ], axis=1)
            print(f"Preprocessed DataFrame shape: {preprocessed_df.shape}")
        except Exception as e:
            print(f"Error combining preprocessed data: {str(e)}")
            return jsonify({"error": f"Error combining preprocessed data: {str(e)}"}), 400
        
        # --------- FEATURE GROUPING LOGIC ----------
        try:
            print("Starting feature grouping")
            test_grouped_data = []

            for i, degree in enumerate(degrees):
                print(f"Processing degree {degree}")
                # Use the encoding dictionary saved from training
                saved_encoding = encoding_dicts[i]
                
                # Apply the encoding to test data
                m, n = preprocessed_df.shape
                print(f"Preprocessed data shape: {m}x{n}")
                new_data = []
                
                for indexes in combinations(range(n), degree):
                    # Only use dict_decode with the saved encoding, not dict_encode
                    new_data.append([dict_decode(saved_encoding, tuple(v), min_occurs) 
                                    for v in preprocessed_df.iloc[:, list(indexes)].values])
                
                test_grouped_data.append(np.array(new_data).T)
                print(f"Added grouped data for degree {degree} with shape {test_grouped_data[-1].shape}")

            # Combine the grouped features
            test_grouped_features = np.hstack(test_grouped_data)
            print(f"Combined grouped features shape: {test_grouped_features.shape}")
            
            test_grouped_df = pd.DataFrame(test_grouped_features, 
                                        columns=[f"grouped_{i}" for i in range(test_grouped_features.shape[1])])
            print(f"Grouped DataFrame shape: {test_grouped_df.shape}")
        except Exception as e:
            print(f"Error in feature grouping: {str(e)}")
            print(traceback.format_exc())
            return jsonify({"error": f"Error in feature grouping: {str(e)}"}), 400

        # Combine with other preprocessed features
        try:
            full_features_df = pd.concat([
                preprocessed_df.reset_index(drop=True), 
                test_grouped_df.reset_index(drop=True)
            ], axis=1)
            print(f"Full features DataFrame shape: {full_features_df.shape}")
        except Exception as e:
            print(f"Error combining with grouped features: {str(e)}")
            return jsonify({"error": f"Error combining with grouped features: {str(e)}"}), 400
        
        # --------- FEATURE SELECTION LOGIC ----------
        try:
            print("Starting feature selection")
            print(f"Full features shape: {full_features_df.shape}")
            print(f"Use column names: {use_column_names}")
            print(f"Max selected index: {max(selected_feature_indices)}")
            
            # Select only the required features that were used during training
            if use_column_names:
                # Use column names if available
                print(f"Selected columns: {len(selected_columns)}")
                print(f"Available columns: {len(full_features_df.columns)}")
                
                # Check if all selected columns are available
                missing_selected_columns = [col for col in selected_columns if col not in full_features_df.columns]
                if missing_selected_columns:
                    print(f"Warning: Missing some selected columns: {missing_selected_columns[:5]}...")
                    # Use only available columns
                    available_selected_columns = [col for col in selected_columns if col in full_features_df.columns]
                    print(f"Using {len(available_selected_columns)} available columns")
                    final_df = full_features_df[available_selected_columns]
                else:
                    final_df = full_features_df[selected_columns]
            else:
                # Otherwise use positional indexing
                if len(full_features_df.columns) > max(selected_feature_indices):
                    print("Using selected feature indices")
                    final_df = full_features_df.iloc[:, selected_feature_indices]
                else:
                    # Handle the case where column count doesn't match
                    print(f"Warning: Data has {len(full_features_df.columns)} columns, but trying to access column {max(selected_feature_indices)}")
                    valid_indices = [idx for idx in selected_feature_indices if idx < len(full_features_df.columns)]
                    print(f"Valid indices: {valid_indices}")
                    final_df = full_features_df.iloc[:, valid_indices]
            
            print(f"Final feature shape: {final_df.shape}")
        except Exception as e:
            print(f"Error in feature selection: {str(e)}")
            print(traceback.format_exc())
            return jsonify({"error": f"Error in feature selection: {str(e)}"}), 400
        
        # Mapping of class indices to field names
        label_map = {
            0: "Law",
            1: "Agriculture",
            2: "Computer Science",
            3: "Medicine",
            4: "Business"
        }

        try:
            print("Making prediction with model")
            prediction = rf_model.predict(final_df)
            predicted_class = int(prediction[0])
            predicted_field = label_map.get(predicted_class, "Unknown")
            print(f"Predicted class: {predicted_class}, field: {predicted_field}")

            # Get probabilities if the model supports it
            try:
                probabilities = rf_model.predict_proba(final_df)[0]
                probs_dict = {label_map[i]: float(prob) for i, prob in enumerate(probabilities)}
                print(f"Prediction probabilities: {probs_dict}")
            except Exception as e:
                print(f"Warning: Could not get prediction probabilities: {str(e)}")
                probs_dict = {}
        except Exception as e:
            print(f"Error making prediction: {str(e)}")
            print(traceback.format_exc())
            return jsonify({"error": f"Error making prediction: {str(e)}"}), 400

        print("Prediction completed successfully")
        return jsonify({
            "prediction": predicted_class,
            "field": predicted_field,
            "probabilities": probs_dict
        })

    except Exception as e:
        print(f"Unexpected error in predict route: {str(e)}")
        print(traceback.format_exc())
        return jsonify({"error": str(e), "traceback": traceback.format_exc()}), 500

# -------------------- Gemini Assessment Routes --------------------
@app.route('/start_assessment', methods=['POST'])
def start_assessment():
    global chat_session, current_field, assessment_in_progress, question_count
    
    try:
        # Check if Gemini is available
        if not gemini_available:
            return jsonify({"error": "Gemini API is not available. Check server logs."}), 500
            
        # Get data from request
        data = request.get_json()
        if not data or 'field' not in data:
            return jsonify({"error": "Field is required"}), 400
            
        # Reset conversation state
        question_count = 0
        assessment_in_progress = True
        
        # Get the field directly from the request
        current_field = data['field']
        
        # Initialize chat session with empty history
        chat_session = gemini_model.start_chat(history=[])
        
        # Send system prompt as the first message - removed min/max question constraints
        initial_prompt = f"""
        You are an assessment assistant for the field of {current_field}. Your task is to assess if the user is suitable for a career in {current_field}.
        
        Instructions:
        1. Ask one question at a time about their skills, interests, and experience relevant to {current_field}.
        2. Make your questions conversational, engaging, and specific to different aspects of {current_field}.
        3. You are free to ask as many questions as you need to make a thorough assessment. Use your judgment to determine when you have enough information.
        4. Ensure your questions cover various aspects like aptitude, interest, relevant experience, and personality fit.
        5. Each question should be concise and direct - aim for 1-3 sentences per question.
        6. Ask your first question directly without an introduction.
        7. When you feel you have enough information to make a proper assessment, end your response with the text "ASSESSMENT_READY".
        """
        
        # Send the initial prompt to set up the context
        chat_session.send_message(initial_prompt)
        
        # Get the first question
        first_q_prompt = "Please ask your first question to assess the user's suitability for this field."
        response = chat_session.send_message(first_q_prompt)
        question_count += 1
        
        return jsonify({
            "response": response.text, 
            "response_type": "question",
            "assessment_complete": False,
            "question_number": question_count
        })
    except Exception as e:
        print(f"Assessment initialization error: {str(e)}")
        print(traceback.format_exc())
        return jsonify({"error": f"Assessment initialization error: {str(e)}"}), 500

@app.route('/chat', methods=['POST'])
def chat():
    global chat_session, current_field, question_count, assessment_in_progress
    
    try:
        if not gemini_available:
            return jsonify({"error": "Gemini API is not available. Check server logs."}), 500
            
        if not chat_session or not current_field or not assessment_in_progress:
            return jsonify({
                "response": "Please start the assessment first by selecting a field.", 
                "response_type": "error",
                "assessment_complete": False
            }), 400

        # Get data from request
        data = request.get_json()
        if not data or 'message' not in data:
            return jsonify({"error": "Message is required"}), 400
            
        # Get the user's message
        user_message = data['message']
        
        # Send user message and include instructions for next question in the same prompt
        # Removed references to min_questions and max_questions constraints
        combined_prompt = f"""
        User response: {user_message}

        Based on this response, please assess the information provided and then ask the next appropriate question 
        to continue evaluating the user's suitability for {current_field}.
        
        Remember:
        1. Make your question conversational and relevant to a different aspect of the field than previously covered.
        2. This is question #{question_count + 1}. You've asked {question_count} questions so far.
        3. You are free to continue asking questions until you feel you have enough information to make a thorough assessment.
        4. Use your judgment to decide when you have sufficient information about the user's skills, interests, and aptitude.
        5. When you feel you have enough information to make a proper assessment, end your response with the text "ASSESSMENT_READY".
        """
        
        # Safety check to prevent infinite questioning (server-side protection)
        if question_count >= absolute_max_questions:
            combined_prompt += "\n\nYou have asked many questions already. Please conclude your assessment after this question by adding ASSESSMENT_READY to your response."
        
        # Send message to LLM - only one API call
        response = chat_session.send_message(combined_prompt)
        question_count += 1
        
        # Check if the LLM indicated it's ready for final assessment
        # or if we've reached absolute max questions
        assessment_ready = "ASSESSMENT_READY" in response.text or question_count >= absolute_max_questions
        
        if assessment_ready:
            # Clean the response if needed
            cleaned_response = response.text.replace("ASSESSMENT_READY", "").strip()
            
            # Generate final assessment
            final_prompt = f"""
            Based on all the responses from the user, provide a comprehensive assessment of their suitability for a career in {current_field}.
            
            Your assessment must follow this exact structure with these exact headings and use JSON format:

            Return ONLY a valid JSON object with these exact keys and nothing else:
            {{
                "suitability_score": "A number from 0-100 representing percentage match",
                "strengths": ["strength1", "strength2", "strength3", ...],
                "areas_for_improvement": ["area1", "area2", "area3", ...],
                "recommendation": "YES or NO",
                "recommendation_reason": "Brief explanation of your recommendation",
                "alternative_fields": ["field1", "field2", "field3"],
                "next_steps": ["step1", "step2", "step3"]
            }}

            Make sure your response is valid JSON that can be parsed. No markdown, no explanations outside the JSON.
            """
            
            assessment_response = chat_session.send_message(final_prompt)
            assessment_in_progress = False
            
            # Extract JSON from the response
            try:
                import json
                import re
                
                # Find JSON pattern in the response
                json_match = re.search(r'({[\s\S]*})', assessment_response.text)
                if json_match:
                    assessment_json = json.loads(json_match.group(1))
                    
                    # Return structured assessment data
                    return jsonify({
                        "response_type": "final_assessment",
                        "assessment_complete": True,
                        "field": current_field,
                        "assessment_data": {
                            "suitability_score": assessment_json.get("suitability_score", "N/A"),
                            "strengths": assessment_json.get("strengths", []),
                            "areas_for_improvement": assessment_json.get("areas_for_improvement", []),
                            "recommendation": assessment_json.get("recommendation", "N/A"),
                            "recommendation_reason": assessment_json.get("recommendation_reason", "N/A"),
                            "alternative_fields": assessment_json.get("alternative_fields", []),
                            "next_steps": assessment_json.get("next_steps", [])
                        },
                        # Include full text as backup
                        "full_response": assessment_response.text
                    })
                else:
                    # Fallback if JSON extraction fails
                    return jsonify({
                        "response": assessment_response.text,
                        "response_type": "final_assessment",
                        "assessment_complete": True,
                        "field": current_field,
                        "parsing_error": "Could not parse structured data from response"
                    })
            except Exception as e:
                # Fallback if JSON processing fails
                return jsonify({
                    "response": assessment_response.text,
                    "response_type": "final_assessment",
                    "assessment_complete": True,
                    "field": current_field,
                    "parsing_error": str(e)
                })
        else:
            # Return the response directly without sending another prompt
            return jsonify({
                "response": response.text,
                "response_type": "question",
                "assessment_complete": False,
                "question_number": question_count
            })
    except Exception as e:
        print(f"Chat error: {str(e)}")
        print(traceback.format_exc())
        return jsonify({"error": f"Chat error: {str(e)}"}), 500
    
# -------------------- Run App --------------------
if __name__ == '__main__':
    app.config['PROPAGATE_EXCEPTIONS'] = True  # Propagate exceptions to see detailed errors
    app.run(debug=True)