import os
import json
import datetime
from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS
from openai import OpenAI
import PyPDF2
import io
import re
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# --- CONFIGURATION ---
# Get API key from environment variable or use placeholder
# Set your API key in the .env file: OPENAI_API_KEY=sk-your-key-here
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") 

client = OpenAI(api_key=OPENAI_API_KEY)

app = Flask(__name__)
CORS(app)

# Create a local folder to save reports
SAVE_FOLDER = "assessments"
if not os.path.exists(SAVE_FOLDER):
    os.makedirs(SAVE_FOLDER)

# --- HELPER: EXTRACT STANDARD NAME FROM FILENAME ---
def extract_standard_name(filename):
    """Extract standard name (e.g., AS9100D, ISO_14001) from filename"""
    base_filename = os.path.splitext(filename)[0]
    # Look for common standard patterns
    patterns = [
        r'(AS9100[Dd]?)',
        r'(ISO[_\s]?\d{5})',
        r'(ISO[_\s]?14001)',
        r'(ISO[_\s]?9001)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, base_filename, re.IGNORECASE)
        if match:
            standard = match.group(1).upper().replace(' ', '_')
            return standard
    
    # If no standard found, use base filename
    return re.sub(r'[^a-zA-Z0-9]', '_', base_filename)

# --- HELPER: FIND EXISTING ASSESSMENT ---
def find_existing_assessment(filename):
    """Find the most recent assessment for a given filename or standard"""
    # Remove file extension and create safe name
    base_filename = os.path.splitext(filename)[0]
    safe_name = re.sub(r'[^a-zA-Z0-9]', '_', base_filename)
    
    # Extract standard name (e.g., AS9100D)
    standard_name = extract_standard_name(filename)
    
    print(f"Looking for existing assessment for: {filename}")
    print(f"  Safe name: {safe_name}")
    print(f"  Standard name: {standard_name}")
    
    matching_files = []
    if os.path.exists(SAVE_FOLDER):
        all_files = os.listdir(SAVE_FOLDER)
        print(f"  Found {len(all_files)} files in assessments folder")
        
        for file in all_files:
            # Check if file matches the pattern and is not a compliance report
            file_lower = file.lower()
            if file.endswith(".json") and "_compliance_report" not in file:
                # Match by standard name (e.g., AS9100D) or by safe name prefix
                if (standard_name and standard_name in file.upper()) or file.startswith(safe_name + "_"):
                    file_path = os.path.join(SAVE_FOLDER, file)
                    matching_files.append((file_path, os.path.getmtime(file_path)))
                    print(f"  ✓ Found matching file: {file}")
    
    if matching_files:
        # Sort by modification time (most recent first)
        matching_files.sort(key=lambda x: x[1], reverse=True)
        print(f"  → Returning most recent file: {os.path.basename(matching_files[0][0])}")
        return matching_files[0][0]
    else:
        print(f"  ✗ No matching assessment files found")
    
    return None

# --- HELPER: LOAD ASSESSMENT FROM FILE ---
def load_assessment(file_path):
    """Load assessment data from a JSON file"""
    try:
        print(f"Loading assessment from: {file_path}")
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Handle different data formats
        if isinstance(data, list):
            print(f"  Loaded {len(data)} clause assessments")
            return data
        elif isinstance(data, dict):
            # If it has assessments key, return that
            if 'assessments' in data:
                return data['assessments']
            # Otherwise, assume the whole dict is assessments
            else:
                return [data]
        else:
            print(f"  Unexpected data type: {type(data)}")
            return None
             
    except Exception as e:
        print(f"Error loading assessment from {file_path}: {e}")
        import traceback
        traceback.print_exc()
        return None

# --- HELPER: SAVE TO LOCAL SYSTEM ---
def save_locally(filename, data):
    """Saves the assessment JSON to your hard drive"""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    base_filename = os.path.splitext(filename)[0]
    safe_name = re.sub(r'[^a-zA-Z0-9]', '_', base_filename)
    save_path = os.path.join(SAVE_FOLDER, f"{safe_name}_{timestamp}.json")
    
    with open(save_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4)
    
    return save_path

# --- HELPER: SAVE COMPLIANCE REPORT ---
def save_compliance_report(filename, report_data):
    """Saves the compliance report JSON to your hard drive"""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    base_filename = os.path.splitext(filename)[0]
    safe_name = re.sub(r'[^a-zA-Z0-9]', '_', base_filename)
    save_path = os.path.join(SAVE_FOLDER, f"{safe_name}_compliance_report_{timestamp}.json")
    
    with open(save_path, 'w', encoding='utf-8') as f:
        json.dump(report_data, f, indent=4)
    
    return save_path

# --- HELPER: FIND EXISTING COMPLIANCE REPORT ---
def find_existing_compliance_report(filename):
    """Find the most recent compliance report for a given filename"""
    base_filename = os.path.splitext(filename)[0]
    safe_name = re.sub(r'[^a-zA-Z0-9]', '_', base_filename)
    standard_name = extract_standard_name(filename)
    
    matching_files = []
    if os.path.exists(SAVE_FOLDER):
        all_files = os.listdir(SAVE_FOLDER)
        for file in all_files:
            if file.endswith(".json") and "_compliance_report" in file:
                if (standard_name and standard_name in file.upper()) or safe_name in file:
                    file_path = os.path.join(SAVE_FOLDER, file)
                    matching_files.append((file_path, os.path.getmtime(file_path)))
    
    if matching_files:
        matching_files.sort(key=lambda x: x[1], reverse=True)
        return matching_files[0][0]
    
    return None

# --- HELPER: GENERATE COMPLIANCE REPORT ---
def generate_compliance_report(assessments):
    """Analyzes all maturity assessments and generates a compliance report based on actual user selections"""
    if not assessments:
        return None
    
    # Calculate overall maturity score using actual selections and practice scores
    total_weighted_score = 0
    total_max_score = 0
    total_level_score = 0
    clause_count = 0
    critical_gaps = []
    moderate_gaps = []
    minor_gaps = []
    assessed_clauses = []
    
    for assessment in assessments:
        clause = assessment.get('clause', '')
        clause_name = clause.split(' ', 1)[1] if ' ' in clause else clause
        clause_num = clause.split(' ')[0] if ' ' in clause else clause
        
        # Get current maturity level from user selection (default to 1 if not selected)
        current_level = assessment.get('selected_maturity_level')
        if current_level is None:
            current_level = 1
        else:
            current_level = int(current_level) if isinstance(current_level, (int, float)) else 1
        
        target_level = 4
        
        # Get actual calculated score from practice selections
        calculated_score = assessment.get('calculated_score', {})
        if not isinstance(calculated_score, dict):
            calculated_score = {}
        
        score_percentage = calculated_score.get('percentage', 0) or 0
        total_score = calculated_score.get('total', 0) or 0
        max_score = calculated_score.get('max', 0) or 0
        
        # Ensure all values are numeric
        try:
            score_percentage = float(score_percentage) if score_percentage is not None else 0.0
            total_score = float(total_score) if total_score is not None else 0.0
            max_score = float(max_score) if max_score is not None else 0.0
        except (ValueError, TypeError):
            score_percentage = 0.0
            total_score = 0.0
            max_score = 0.0
        
        # Calculate weighted maturity level (considering practice scores)
        # If user selected a level but practices aren't complete, adjust level down
        if current_level > 1 and score_percentage < 50:
            # If practices are less than 50% complete, reduce effective level
            effective_level = max(1, current_level - 0.5)
        elif current_level > 1 and score_percentage < 75:
            effective_level = current_level - 0.25
        else:
            effective_level = current_level
        
        # Add to weighted scores
        if max_score > 0:
            total_weighted_score += total_score
            total_max_score += max_score
        
        total_level_score += effective_level
        clause_count += 1
        
        # Calculate gap
        gap = target_level - effective_level
        
        # Get maturity level descriptions and practices from assessment
        maturity_levels = assessment.get('maturity_levels', [])
        current_level_data = None
        level_2_data = None
        level_3_data = None
        level_4_data = None
        
        for ml in maturity_levels:
            ml_level = ml.get('level')
            if ml_level == current_level:
                current_level_data = ml
            if ml_level == 2:
                level_2_data = ml
            if ml_level == 3:
                level_3_data = ml
            if ml_level == 4:
                level_4_data = ml
        
        # Check if level was actually selected
        level_selected = assessment.get('selected_maturity_level') is not None
        
        # Build structured gap analysis based on user selection
        user_selection_summary = []
        missing_at_current_level = []
        roadmap_to_level_4 = []
        practices_by_level = {}
        
        # 1. What user selected
        if not level_selected:
            user_selection_summary.append({
                "title": "Your Selection",
                "content": "No maturity level has been selected. Default status: Level 1.",
                "level": 1
            })
        else:
            user_selection_summary.append({
                "title": "Your Selection",
                "content": f"You selected Level {current_level}. {current_level_data.get('description', '') if current_level_data else ''}",
                "level": current_level
            })
        
        # 2. Practice completion status
        if max_score > 0 and score_percentage > 0:
            user_selection_summary.append({
                "title": "Practice Completion",
                "content": f"You have completed {score_percentage:.1f}% of practices ({total_score:.2f} out of {max_score:.2f} points).",
                "percentage": score_percentage
            })
        else:
            user_selection_summary.append({
                "title": "Practice Completion",
                "content": "Practices have not been assessed or completed yet.",
                "percentage": 0
            })
        
        # 3. What's missing at current level
        if current_level_data:
            current_practices = current_level_data.get('practices', [])
            if score_percentage < 100 and current_practices:
                # Find practices not completed (if we had that data, for now show all practices)
                missing_at_current_level.append(f"Complete remaining practices for Level {current_level}")
                missing_at_current_level.append(f"Ensure all Level {current_level} practices are implemented")
        
        # 4. Build step-by-step roadmap to Level 4
        if current_level < 2 and level_2_data:
            roadmap_to_level_4.append({
                "step": 1,
                "target_level": 2,
                "title": f"Step 1: Advance to Level 2",
                "description": level_2_data.get('description', ''),
                "practices": [p.get('text', '') for p in level_2_data.get('practices', [])[:4]],  # Top 4 practices
                "what_to_do": f"Implement Level 2 practices: {', '.join([p.get('text', '') for p in level_2_data.get('practices', [])[:3]])}"
            })
        
        if current_level < 3 and level_3_data:
            roadmap_to_level_4.append({
                "step": 2 if current_level < 2 else 1,
                "target_level": 3,
                "title": f"Step {'2' if current_level < 2 else '1'}: Advance to Level 3",
                "description": level_3_data.get('description', ''),
                "practices": [p.get('text', '') for p in level_3_data.get('practices', [])[:4]],
                "what_to_do": f"Implement Level 3 practices: {', '.join([p.get('text', '') for p in level_3_data.get('practices', [])[:3]])}"
            })
        
        if current_level < 4 and level_4_data:
            roadmap_to_level_4.append({
                "step": 3 if current_level < 3 else (2 if current_level < 2 else 1),
                "target_level": 4,
                "title": f"Step {3 if current_level < 3 else (2 if current_level < 2 else 1)}: Reach Level 4 (Target)",
                "description": level_4_data.get('description', ''),
                "practices": [p.get('text', '') for p in level_4_data.get('practices', [])[:4]],
                "what_to_do": f"Implement Level 4 practices: {', '.join([p.get('text', '') for p in level_4_data.get('practices', [])[:3]])}"
            })
        
        # Build gap description
        gap_description_parts = []
        if level_selected:
            gap_description_parts.append(f"You selected Level {current_level}.")
            if current_level_data:
                gap_description_parts.append(f"Current state: {current_level_data.get('description', '')}")
        else:
            gap_description_parts.append("No maturity level selected. Default: Level 1.")
        
        if max_score > 0:
            if score_percentage == 0:
                gap_description_parts.append("No practices completed.")
            else:
                gap_description_parts.append(f"Practice completion: {score_percentage:.1f}%.")
        else:
            gap_description_parts.append("Practices not assessed.")
        
        gap_description = " ".join(gap_description_parts)
        
        # Ensure gap is a valid number
        gap = float(gap) if gap is not None else float(target_level - effective_level)
        
        # Store structured gap details
        gap_details = {
            "user_selection": user_selection_summary,
            "missing_at_current_level": missing_at_current_level,
            "roadmap_to_level_4": roadmap_to_level_4,
            "current_level_description": current_level_data.get('description', '') if current_level_data else '',
            "level_2_description": level_2_data.get('description', '') if level_2_data else '',
            "level_3_description": level_3_data.get('description', '') if level_3_data else '',
            "level_4_description": level_4_data.get('description', '') if level_4_data else ''
        }
        
        gap_info = {
            "clause": clause_num,
            "clause_name": clause_name,
            "current_level": round(effective_level, 1),
            "selected_level": current_level,
            "target_level": target_level,
            "gap_description": gap_description,
            "score_percentage": round(score_percentage, 1) if score_percentage is not None else 0.0,
            "total_score": round(total_score, 2) if total_score is not None else 0.0,
            "max_score": round(max_score, 2) if max_score is not None else 0.0,
            "impact": "High" if gap >= 2.5 else "Medium" if gap >= 1.5 else "Low",
            "priority": "Critical" if gap >= 2.5 else "High" if gap >= 1.5 else "Medium" if gap >= 0.5 else "Low",
            "gap_details": gap_details
        }
        
        assessed_clauses.append(gap_info)
        
        if gap >= 2.5:
            critical_gaps.append(gap_info)
        elif gap >= 1.5:
            moderate_gaps.append(gap_info)
        else:
            minor_gaps.append(gap_info)
    
    # Calculate overall maturity score (weighted average)
    if clause_count > 0:
        avg_level = total_level_score / clause_count
        # Also calculate percentage-based score
        overall_percentage = (total_weighted_score / total_max_score * 100) if total_max_score > 0 else 0
        # Combine level and percentage for final score
        combined_score = (avg_level / 4 * 100 * 0.6) + (overall_percentage * 0.4)
        overall_maturity = f"Level {avg_level:.1f}"
        overall_maturity_numeric = avg_level
        overall_percentage_score = round(combined_score, 1)
    else:
        overall_maturity = "Level 1.0"
        overall_maturity_numeric = 1.0
        overall_percentage_score = 0
    
    # Generate recommendations using detailed gap information
    recommendations = []
    for gap in critical_gaps + moderate_gaps + minor_gaps:
        clause_num = gap.get('clause', '')
        current = gap.get('current_level', 1)
        selected = gap.get('selected_level', 1)
        target = gap.get('target_level', 4)
        gap_details = gap.get('gap_details', {})
        
        # Ensure current is a valid number
        try:
            current = float(current) if current is not None else 1.0
            selected = float(selected) if selected is not None else 1.0
        except (ValueError, TypeError):
            current = 1.0
            selected = 1.0
        
        gap_size = float(target) - float(current)
        
        # Build action items from gap details
        action_items = []
        
        # Add specific actions based on current level
        if selected < 2:
            action_items.append(f"Immediate: Select and implement Level 2 maturity for {gap.get('clause_name', clause_num)}")
            if gap_details.get('required_actions'):
                for action in gap_details['required_actions']:
                    if 'Level 2' in action or 'Level 1' in action:
                        action_items.append(f"  • {action}")
        
        if selected < 3:
            action_items.append(f"Short-term: Advance to Level 3 by implementing standardized frameworks")
            if gap_details.get('practices_to_implement'):
                action_items.append(f"  • Key practices to implement: {', '.join(gap_details['practices_to_implement'][:2])}")
        
        if selected < 4:
            action_items.append(f"Long-term: Achieve Level 4 maturity through automation and optimization")
            if gap_details.get('target_level_description'):
                action_items.append(f"  • Target state: {gap_details['target_level_description']}")
        
        # Add missing elements as action items
        if gap_details.get('missing_elements'):
            action_items.append(f"Address missing elements: {', '.join(gap_details['missing_elements'][:3])}")
        
        # If no specific actions, provide generic ones
        if not action_items:
            if current < 2:
                action_items.append(f"Establish basic documentation and processes for {gap.get('clause_name', clause_num)} to reach Level 2.")
            if current < 3:
                action_items.append(f"Develop standardized frameworks and integrate {gap.get('clause_name', clause_num)} into planning processes to reach Level 3.")
            if current < 4:
                action_items.append(f"Implement automation and continuous improvement for {gap.get('clause_name', clause_num)} to reach Level 4.")
        
        # Determine timeline based on gap size
        if gap_size >= 2.5:
            timeline = "6-12 months"
        elif gap_size >= 1.5:
            timeline = "3-6 months"
        else:
            timeline = "1-3 months"
        
        recommendations.append({
            "clause": clause_num,
            "clause_name": gap.get('clause_name', clause_num),
            "current_level": current,
            "selected_level": selected,
            "target_level": target,
            "action_items": action_items,
            "timeline": timeline,
            "missing_elements": gap_details.get('missing_elements', []),
            "practices_to_implement": gap_details.get('practices_to_implement', []),
            "resources_required": f"Training, documentation tools, process improvement resources, and management commitment for {gap.get('clause_name', clause_num)}."
        })
    
    # Generate roadmap
    roadmap = {
        "phase_1": {
            "title": "Foundation (Level 1 to Level 2)",
            "duration": "3-6 months",
            "clauses": [g['clause'] for g in critical_gaps[:5] + moderate_gaps[:3]],
            "key_actions": [
                "Document basic processes and procedures",
                "Establish foundational documentation",
                "Identify key stakeholders and requirements"
            ]
        },
        "phase_2": {
            "title": "Standardization (Level 2 to Level 3)",
            "duration": "6-9 months",
            "clauses": [g['clause'] for g in critical_gaps + moderate_gaps],
            "key_actions": [
                "Develop standardized frameworks",
                "Integrate processes into strategic planning",
                "Establish regular review processes"
            ]
        },
        "phase_3": {
            "title": "Optimization (Level 3 to Level 4)",
            "duration": "6-12 months",
            "clauses": [g['clause'] for g in critical_gaps + moderate_gaps + minor_gaps],
            "key_actions": [
                "Implement automation and monitoring systems",
                "Use data-driven decision making",
                "Align all processes with strategic objectives"
            ]
        }
    }
    
    # Generate priority matrix
    priority_matrix = {
        "quick_wins": [f"{g['clause']} - {g['clause_name']}" for g in minor_gaps[:5]],
        "strategic_initiatives": [f"{g['clause']} - {g['clause_name']}" for g in critical_gaps[:5]],
        "foundational_requirements": [f"{g['clause']} - {g['clause_name']}" for g in moderate_gaps[:5]]
    }
    
    # Generate executive summary
    executive_summary = f"The organization is currently at {overall_maturity} maturity level with {len(critical_gaps)} critical gaps, {len(moderate_gaps)} moderate gaps, and {len(minor_gaps)} minor gaps. "
    executive_summary += f"Key focus areas include {', '.join([g['clause_name'] for g in critical_gaps[:3]])} which require immediate attention to reach Level 4 compliance."
    
    # Generate executive summary based on actual scores
    total_gaps = len(critical_gaps) + len(moderate_gaps) + len(minor_gaps)
    completion_rate = (clause_count - total_gaps) / clause_count * 100 if clause_count > 0 else 0
    
    executive_summary = f"Based on your assessment selections, the organization is currently at {overall_maturity} maturity level "
    executive_summary += f"with an overall compliance score of {overall_percentage_score:.1f}%. "
    executive_summary += f"Out of {clause_count} clauses assessed, {len(critical_gaps)} require critical attention, "
    executive_summary += f"{len(moderate_gaps)} have moderate gaps, and {len(minor_gaps)} have minor gaps. "
    if critical_gaps:
        executive_summary += f"Priority focus areas include {', '.join([g['clause'] + ' - ' + g['clause_name'] for g in critical_gaps[:3]])}."
    
    report = {
        "executive_summary": executive_summary,
        "overall_maturity_score": overall_maturity,
        "overall_maturity_numeric": round(overall_maturity_numeric, 2),
        "overall_percentage_score": overall_percentage_score,
        "total_clauses": clause_count,
        "assessed_clauses": assessed_clauses,
        "gap_analysis": {
            "critical_gaps": critical_gaps,
            "moderate_gaps": moderate_gaps,
            "minor_gaps": minor_gaps
        },
        "recommendations": recommendations,
        "roadmap_to_level_4": roadmap,
        "priority_matrix": priority_matrix
    }
    
    return report

# --- HELPER: EXTRACT TEXT ---
def extract_text_from_pdf(file_bytes):
    """Extract text from entire PDF document"""
    try:
        reader = PyPDF2.PdfReader(io.BytesIO(file_bytes))
        text = ""
        total_pages = len(reader.pages)
        print(f"Reading entire PDF: {total_pages} pages")
        
        # Read all pages
        for i, page in enumerate(reader.pages):
            page_text = page.extract_text()
            if page_text.strip():  # Only add non-empty pages
                text += f"\n--- Page {i+1} ---\n" + page_text + "\n"
            if (i + 1) % 10 == 0:
                print(f"  Processed {i+1}/{total_pages} pages...")
        
        print(f"✓ Extracted {len(text)} characters from {total_pages} pages")
        return text
    except Exception as e:
        print(f"Error extracting PDF text: {e}")
        import traceback
        traceback.print_exc()
        return ""

# --- HELPER: FIND CLAUSE CONTENT IN TEXT ---
def find_clause_content(text, clause_number):
    """Find the content section for a specific clause in the document text"""
    import re
    
    # Try different patterns to find clause
    patterns = [
        (f"\\b{re.escape(clause_number)}\\b", 0),  # Exact match with word boundary
        (f"{re.escape(clause_number)}\\s", 0),     # With space
        (f"Clause\\s+{re.escape(clause_number)}", 0),  # "Clause 4.1"
        (f"Section\\s+{re.escape(clause_number)}", 0), # "Section 4.1"
        (f"{re.escape(clause_number)}\\.", 0),    # With period
    ]
    
    best_match = None
    best_position = -1
    best_length = 0
    
    for pattern, priority in patterns:
        matches = list(re.finditer(pattern, text, re.IGNORECASE))
        if matches:
            # Use the match with highest priority (lower number) or first occurrence
            for match in matches:
                if best_position == -1 or (match.start() < best_position and priority == 0):
                    best_position = match.start()
                    best_match = match
                    best_length = len(match.group())
    
    if best_match:
        # Extract content around the clause (500 chars before, 2500 chars after)
        start = max(0, best_match.start() - 500)
        # Find the end of this clause section (look for next clause or section break)
        remaining_text = text[best_match.end():]
        next_clause_match = re.search(r'\b\d+\.\d+\b', remaining_text[:3000])
        if next_clause_match:
            end = best_match.end() + next_clause_match.start()
        else:
            end = min(len(text), best_match.end() + 2500)
        
        return text[start:end]
    
    return None

# --- HELPER: EXTRACT CLAUSE TEXT WITH ALL REQUIREMENTS ---
def extract_full_clause_text(text, clause_number):
    """Extract the complete clause text including all sub-points and requirements"""
    import re
    
    # Find the clause start
    patterns = [
        rf'\b{re.escape(clause_number)}\b',
        rf'{re.escape(clause_number)}\s',
        rf'Clause\s+{re.escape(clause_number)}',
        rf'Section\s+{re.escape(clause_number)}',
    ]
    
    best_match = None
    best_position = -1
    
    for pattern in patterns:
        matches = list(re.finditer(pattern, text, re.IGNORECASE))
        if matches:
            match = matches[0]
            if best_position == -1 or match.start() < best_position:
                best_position = match.start()
                best_match = match
    
    if best_match:
        # Extract from clause start to next clause or section
        start = max(0, best_match.start() - 200)
        remaining = text[best_match.end():]
        
        # Find the end - look for next clause number (e.g., 4.2, 5.1, etc.)
        next_clause_pattern = r'\b\d+\.\d+\b'
        next_match = re.search(next_clause_pattern, remaining[:5000])
        
        if next_match:
            end = best_match.end() + next_match.start()
        else:
            # If no next clause found, take a large chunk
            end = min(len(text), best_match.end() + 5000)
        
        return text[start:end]
    
    return None

# --- HELPER: GENERATE ASSESSMENTS FOR A SECTION ---
def generate_section_assessments(text, section_name, clauses):
    """Generate ISO audit-compliant assessments by parsing clauses line-by-line and extracting all requirements"""
    clauses_str = ", ".join(clauses)
    
    # Build comprehensive clause content with full text
    clause_contents = []
    for clause in clauses:
        clause_num = clause.split('.')[0] if '.' in clause else clause
        clause_content = extract_full_clause_text(text, clause)
        if clause_content:
            clause_contents.append(f"\n{'='*80}\nFULL TEXT FOR CLAUSE {clause}:\n{'='*80}\n{clause_content}\n")
        else:
            # Fallback to general search
            clause_content = find_clause_content(text, clause)
            if clause_content:
                clause_contents.append(f"\n{'='*80}\nTEXT FOR CLAUSE {clause}:\n{'='*80}\n{clause_content}\n")
            else:
                clause_contents.append(f"\n{'='*80}\nCLAUSE {clause} (using general context):\n{'='*80}\n{text[:2000]}\n")
    
    clause_text = "\n".join(clause_contents)
    general_text = text[:15000]  # More context
    
    prompt = f"""
You are an ISO 9001/AS9100 Lead Auditor conducting a certification audit. Your task is to create a COMPLETE, AUDIT-READY assessment for {section_name} clauses: {clauses_str}.

CRITICAL MANDATORY PROCESS - FOLLOW EXACTLY:

STEP 1: PARSE EACH CLAUSE LINE-BY-LINE
- Read the FULL clause text provided below
- Identify EVERY line that contains the word "shall" (explicit requirements)
- Identify EVERY sub-clause with its ACTUAL numbering as it appears in the document (e.g., 7.3.1, 7.3.2, 7.3.3, NOT 7.3 a, 7.3 b)
- Extract sub-points EXACTLY as numbered in the document (e.g., if document shows 7.3.1, use "7.3.1", not "7.3 a")
- Treat EACH sub-clause as an INDEPENDENT, MANDATORY requirement
- Do NOT merge, combine, or skip any requirement
- Preserve the EXACT clause numbering structure from the document

STEP 2: EXTRACT ALL REQUIREMENTS
For EACH clause, create a "requirements" array with:
- Main requirement (the primary "shall" statement for the main clause, e.g., "7.3")
- Sub-requirements (each numbered sub-clause as it appears: 7.3.1, 7.3.2, 7.3.3, etc.)
- Use the EXACT numbering from the document - do NOT convert to letters
- Implicit requirements (requirements implied by the clause structure)

STEP 3: CREATE ASSESSMENT QUESTIONS
For EACH requirement, create assessment questions that verify:
- AWARENESS: Does the organization know about this requirement?
- UNDERSTANDING: Does the organization understand what this requirement means?
- APPLICATION: Is the requirement actually implemented and followed?

STEP 4: INCLUDE MANDATORY ELEMENTS
Wherever applicable, ensure assessments cover:
- Product/service conformity requirements
- Product safety requirements
- Ethical behavior requirements
- Consequences of nonconformity
- Customer satisfaction
- Continuous improvement

STEP 5: LINK TO CLAUSE REFERENCES
Each assessment item MUST reference the exact clause location AS IT APPEARS IN THE DOCUMENT:
- Main clause: "{clauses[0]}" (e.g., "7.3")
- Sub-clauses: Use the EXACT numbering from document (e.g., "7.3.1", "7.3.2", "7.3.3", NOT "7.3 a", "7.3 b")
- If document uses letters (a, b, c), convert to numbered format (7.3.1, 7.3.2, 7.3.3) based on position
- Preserve the hierarchical structure: 7.3 → 7.3.1 → 7.3.1.1 if such structure exists

STEP 6: COMPLETENESS VERIFICATION
Before finalizing, verify:
- Every "shall" statement has a corresponding assessment
- Every sub-point (a, b, c...) has an assessment
- No requirement is merged or implied
- All requirements are explicitly assessed

OUTPUT STRUCTURE:

The JSON must be an ARRAY of Clause Objects - one for EACH clause: {clauses_str}

Each Clause Object MUST have:

1. "clause": (String) e.g., "{clauses[0]} Understanding the Organization and Its Context" - MUST match exactly

2. "requirements": (Array) - ONE object for EACH "shall" requirement and sub-clause found in the clause
   Each requirement object must have:
   - "requirement_id": (String) Use EXACT numbering from document (e.g., "{clauses[0]}", "{clauses[0]}.1", "{clauses[0]}.2", "{clauses[0]}.3")
     - If document shows 7.3.1, use "7.3.1" (NOT "7.3 a")
     - If document shows 7.3.2, use "7.3.2" (NOT "7.3 b")
     - Preserve the exact hierarchical numbering structure from the ISO document
   - "requirement_text": (String) The exact or paraphrased requirement text
   - "requirement_type": (String) "explicit" or "implicit"
   - "assessment_questions": (Array of strings) - Questions to verify awareness, understanding, and application
     - Must include questions about: awareness, understanding, application
     - Include product/service conformity, safety, ethics, consequences where applicable
   - "mandatory_elements": (Array of strings) - List applicable elements: ["conformity", "safety", "ethics", "consequences"]

3. "critical_question": (String) Overall yes/no audit question for the entire clause (USER-FACING)
   - MUST be distinct and unique from assessment questions
   - MUST be a high-level compliance decision question
   - MUST NOT repeat wording from assessment questions, maturity descriptions, or practices
   - This is the PRIMARY question shown to users

4. "completeness_statement": (String) INTERNAL VALIDATION ONLY - Must state: "All clause requirements are fully assessed. Total requirements identified: [number]"
   - This is for INTERNAL validation and MUST NEVER be displayed to end users
   - Used only for backend verification that all requirements were captured
   - Hidden from UI completely

5. "maturity_levels": (Array of 4 objects) - ONLY after compliance is ensured
   Each level object must have:
   - "level": (int) 1, 2, 3, or 4
   - "description": (String) What this maturity level means for THIS specific clause
     - MUST be unique and distinct from critical question and practices
     - MUST NOT repeat wording from assessment questions or practices
     - Must describe capability progression, not rephrase requirements
   - "practices": (Array of 6 objects) - WORKPLACE PRACTICES (EVIDENCE-BASED, MANDATORY)
     - Each practice has "text" (string) and "score" (float 0.0 to 1.0)
     - Practices MUST be brief (1-2 lines maximum), concrete, practical, and example-based
     - Practices MUST use evidence-style language showing observable organizational evidence:
       * "The organization has documented [specific item] in [specific location]."
       * "A register is maintained for [specific purpose] and reviewed [frequency]."
       * "Records show that [specific action] was performed on [date/regularly]."
       * "Management review minutes include [specific evidence]."
       * "A procedure exists that defines [specific process]."
       * "Training records indicate [specific competency] for [role]."
     - Practices MUST be observable organizational evidence, NOT rephrased requirements
     - Practices MUST NOT:
       * Rephrase the requirement text
       * Repeat assessment questions or critical questions
       * Use generic statements like "organization identifies issues" or "processes are documented"
       * Repeat wording from critical questions or maturity level descriptions
       * Use vague language - must be specific and evidence-focused
     - Practices MUST be DIFFERENT for each maturity level and show clear progression
     - Examples of CORRECT practices:
       * "External and internal issues are documented in a context register maintained by Quality Manager."
       * "SWOT/PESTLE analysis records are maintained in document control system and reviewed annually."
       * "Identified issues are referenced in management review minutes dated [specific dates]."
       * "Responsibility for issue review is assigned to [role] and recorded in job descriptions."
     - Examples of INCORRECT practices (DO NOT USE):
       * "Organization identifies issues" (too generic)
       * "Processes are documented" (not evidence-based)
       * "The organization shall document issues" (rephrasing requirement)

CRITICAL RULES:
1. You MUST parse the clause text line-by-line
2. You MUST extract EVERY "shall" requirement
3. You MUST use the EXACT clause numbering from the document (e.g., 7.3.1, 7.3.2, NOT 7.3 a, 7.3 b)
4. You MUST treat each numbered sub-clause as an independent requirement
5. You MUST create assessment questions for EACH requirement
6. You MUST link each assessment to the exact clause reference as it appears (e.g., 7.3.1, 7.3.2, 7.3.3)
7. You MUST verify completeness before outputting (completeness_statement is for internal validation only)
8. You MUST state "All clause requirements are fully assessed" in completeness_statement (INTERNAL ONLY - not displayed to users)

CONTENT UNIQUENESS RULES (MANDATORY):
9. DO NOT repeat the same wording, intent, or content across:
   - Assessment questions
   - Critical questions
   - Maturity level descriptions
   - Workplace practices
10. Each section must serve a DISTINCT purpose:
    - Assessment Questions → What is being evaluated (internal use)
    - Critical Question → High-level compliance decision (user-facing)
    - Maturity Levels → Capability progression (user-facing)
    - Workplace Practices → Real, observable examples (user-facing)

WORKPLACE PRACTICES RULES (VERY IMPORTANT):
11. Workplace practices MUST be:
    - Brief (1-2 lines per point)
    - Concrete and practical
    - Example-based
    - Written as observable organizational evidence
    - Use evidence-style language: "The organization has documented...", "A register is maintained...", "Records show that..."
12. Workplace practices MUST NOT:
    - Rephrase the requirement
    - Repeat assessment questions
    - Use generic statements like "organization identifies issues"
    - Repeat wording from critical questions or maturity descriptions
13. Maturity levels are ONLY for process improvement AFTER compliance is ensured
14. The output MUST be suitable for ISO certification audits

EXAMPLE STRUCTURE:
{{
  "clause": "7.3 Design and Development",
  "requirements": [
    {{
      "requirement_id": "7.3",
      "requirement_text": "The organization shall establish, implement and maintain design and development processes...",
      "requirement_type": "explicit",
      "assessment_questions": [
        "Is the organization aware of the requirement to establish design and development processes?",
        "Does the organization understand what design and development processes must include?",
        "Are design and development processes actually implemented and maintained?",
        "How does the organization ensure product/service conformity in design?",
        "What are the consequences of nonconformity in design processes?"
      ],
      "mandatory_elements": ["conformity", "consequences"]
    }},
    {{
      "requirement_id": "7.3.1",
      "requirement_text": "The organization shall determine design and development stages...",
      "requirement_type": "explicit",
      "assessment_questions": [
        "Is the organization aware of the requirement to determine design stages?",
        "Does the organization understand what design stages must be determined?",
        "Are design stages actually determined and documented?"
      ],
      "mandatory_elements": []
    }},
    {{
      "requirement_id": "7.3.2",
      "requirement_text": "The organization shall apply controls to design and development...",
      "requirement_type": "explicit",
      "assessment_questions": [
        "Is the organization aware of controls required for design and development?",
        "Does the organization understand what controls must be applied?",
        "Are controls actually applied to design and development processes?"
      ],
      "mandatory_elements": ["conformity"]
    }}
  ],
  "critical_question": "Has the organization established, implemented and maintained design and development processes?",
  "completeness_statement": "All clause requirements are fully assessed. Total requirements identified: 8",
  "maturity_levels": [...]
}}

NOTE: Use EXACT numbering from document (7.3.1, 7.3.2, 7.3.3) NOT letters (7.3 a, 7.3 b, 7.3 c)

FULL CLAUSE TEXTS:
{clause_text}

GENERAL DOCUMENT CONTEXT:
{general_text}

NOW PARSE EACH CLAUSE LINE-BY-LINE AND GENERATE COMPLETE ASSESSMENTS FOR ALL {len(clauses)} CLAUSES: {clauses_str}
"""
    
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": f"""You are a strict ISO 9001/AS9100 Lead Auditor conducting a certification audit.

CRITICAL MANDATORY PROCESS:

1. PARSE CLAUSES LINE-BY-LINE
   - Read EVERY line of the clause text
   - Extract EVERY "shall" requirement
   - Extract EVERY sub-clause with its EXACT numbering from document (e.g., 7.3.1, 7.3.2, 7.3.3)
   - Use the ACTUAL numbering structure from the document (NOT letters like a, b, c)
   - Do NOT merge, combine, or skip ANY requirement

2. EXTRACT ALL REQUIREMENTS
   - Main requirement (primary "shall" for main clause, e.g., "7.3")
   - Each sub-clause as it appears in document (e.g., "7.3.1", "7.3.2", "7.3.3")
   - Preserve the exact hierarchical numbering from the ISO document
   - Implicit requirements

3. CREATE ASSESSMENT QUESTIONS FOR EACH REQUIREMENT
   - Verify AWARENESS: Does organization know about requirement?
   - Verify UNDERSTANDING: Does organization understand requirement?
   - Verify APPLICATION: Is requirement implemented?
   - Include product/service conformity, safety, ethics, consequences where applicable

4. LINK TO CLAUSE REFERENCES
   - Main: "{clauses[0]}" (e.g., "7.3")
   - Sub-clauses: Use EXACT numbering from document (e.g., "7.3.1", "7.3.2", "7.3.3")
   - Do NOT use letters - use the numbered format as it appears in the document

5. COMPLETENESS CHECK
   - Verify EVERY "shall" has assessment
   - Verify EVERY sub-clause has assessment
   - State: "All clause requirements are fully assessed"

6. MATURITY LEVELS (ONLY AFTER COMPLIANCE)
   - Maturity levels are for process improvement
   - They do NOT replace clause requirements
   - Compliance must be ensured first

OUTPUT REQUIREMENTS:
- Output ONLY valid JSON arrays
- No markdown formatting
- No introductory text
- MUST generate assessments for ALL {len(clauses)} clauses: {clauses_str}
- Each clause MUST have "requirements" array with ALL "shall" statements and sub-points
- Each requirement MUST have assessment_questions array
- MUST include completeness_statement

FINAL QUALITY CHECK BEFORE OUTPUTTING:
1. All {len(clauses)} clauses are assessed: {clauses_str}
2. Every "shall" requirement has an assessment
3. Every sub-clause has an assessment with exact numbering (7.3.1, 7.3.2, etc.)
4. Completeness statement is included (INTERNAL ONLY - not shown to users)
5. All requirements are linked to clause references
6. NO repetition between assessment questions, critical question, maturity descriptions, and practices
7. Workplace practices use evidence-style language and are concrete/observable
8. Requirements section is for internal validation only (not displayed in UI)
9. User-facing content starts with Critical Question, then Maturity Levels and Practices"""},
            {"role": "user", "content": prompt}
        ],
        temperature=0.1,  # Lower temperature for more consistent, audit-focused output
        max_tokens=16384  # Maximum supported by gpt-4o model
    )
    
    raw_content = response.choices[0].message.content
    clean_json = raw_content.replace("```json", "").replace("```", "").strip()
    
    # Try to parse JSON, handle incomplete responses
    try:
        assessment_data = json.loads(clean_json)
        
        # Validate we got all clauses
        if not isinstance(assessment_data, list):
            print(f"Error: Expected list but got {type(assessment_data)}")
            return []
        
        # Validate structure and requirements
        for item in assessment_data:
            clause = item.get('clause', '')
            clause_num = clause.split(' ')[0] if ' ' in clause else clause
            
            # Check if requirements array exists (new structure)
            if 'requirements' in item:
                req_count = len(item.get('requirements', []))
                print(f"  ✓ {clause_num}: Found {req_count} requirements")
                
                # Validate completeness statement
                if not item.get('completeness_statement'):
                    print(f"  ⚠ Warning: {clause_num} missing completeness_statement")
                else:
                    print(f"  ✓ {clause_num}: {item.get('completeness_statement')}")
            else:
                print(f"  ⚠ Warning: {clause_num} missing 'requirements' array (using legacy structure)")
        
        # Check clause numbers
        generated_nums = []
        for item in assessment_data:
            clause = item.get('clause', '')
            clause_num = clause.split(' ')[0] if ' ' in clause else clause
            generated_nums.append(clause_num)
        
        missing = [c for c in clauses if c not in generated_nums]
        if missing:
            print(f"Warning: Missing clauses in response: {missing}")
            print(f"Generated: {generated_nums}")
        
        return assessment_data
        
    except json.JSONDecodeError as e:
        print(f"JSON parse error for {section_name}: {e}")
        print(f"Response length: {len(clean_json)}")
        print(f"First 500 chars: {clean_json[:500]}")
        print(f"Last 500 chars: {clean_json[-500:]}")
        
        # Try to fix incomplete JSON by finding the last complete object
        try:
            # Find the last complete array element
            last_bracket = clean_json.rfind(']')
            if last_bracket > 0:
                fixed_json = clean_json[:last_bracket + 1]
                assessment_data = json.loads(fixed_json)
                print(f"Fixed JSON: Got {len(assessment_data)} items")
                return assessment_data
        except Exception as fix_error:
            print(f"Failed to fix JSON: {fix_error}")
        
        raise

# --- ROUTE: ANALYZE ---
@app.route('/analyze', methods=['POST'])
def analyze():
    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    
    file = request.files['file']
    if not file.filename:
        return jsonify({"error": "No file selected"}), 400

    # 1. Check if assessment already exists for this document
    print(f"\n{'='*60}")
    print(f"Checking for existing assessment for: {file.filename}")
    print(f"{'='*60}")
    
    existing_assessment_path = find_existing_assessment(file.filename)
    existing_compliance_report_path = find_existing_compliance_report(file.filename)
    
    if existing_assessment_path:
        print(f"\n✓ Found existing assessment: {existing_assessment_path}")
        print("Loading saved assessment...")
        saved_assessments = load_assessment(existing_assessment_path)
        
        if saved_assessments:
            print(f"✓ Successfully loaded {len(saved_assessments)} clause assessments from saved file")
            print(f"\n{'='*60}")
            print("Returning cached assessment (no new generation needed)")
            print(f"{'='*60}\n")
            return jsonify(saved_assessments)
        else:
            print("✗ Failed to load existing assessment (file may be corrupted), will generate new one...")
    else:
        print(f"\n✗ No existing assessment found for: {file.filename}")
        print("Will generate new assessment...\n")
    
    # 2. Read PDF (only if no existing assessment found)
    print(f"Reading {file.filename}...")
    file.seek(0)  # Reset file pointer
    text = extract_text_from_pdf(file.read())
    if len(text) < 50:
        return jsonify({"error": "PDF seems empty or unreadable."}), 400

    # 3. Generate assessments section by section
    print("Generating assessments section by section...")
    try:
        # Define all sections and their clauses
        sections = [
            ("Section 4", ["4.1", "4.2", "4.3", "4.4"]),
            ("Section 5", ["5.1", "5.2", "5.3"]),
            ("Section 6", ["6.1", "6.2", "6.3"]),
            ("Section 7", ["7.1", "7.2", "7.3", "7.4", "7.5", "7.6"]),
            ("Section 8", ["8.1", "8.2", "8.3", "8.4", "8.5", "8.6", "8.7"]),
            ("Section 9", ["9.1", "9.2", "9.3"]),
            ("Section 10", ["10.1", "10.2", "10.3"])
        ]
        
        all_assessments = []
        
        for section_name, clauses in sections:
            print(f"Generating assessments for {section_name} (clauses: {', '.join(clauses)})...")
            try:
                section_data = generate_section_assessments(text, section_name, clauses)
                if isinstance(section_data, list):
                    # Validate that all clauses were generated
                    generated_clause_nums = [item.get('clause', '').split(' ')[0] if ' ' in item.get('clause', '') else item.get('clause', '') for item in section_data]
                    missing_clauses = [c for c in clauses if c not in generated_clause_nums]
                    
                    if missing_clauses:
                        print(f"  ⚠ Warning: Missing clauses in {section_name}: {missing_clauses}")
                        print(f"  Generated: {generated_clause_nums}")
                        print(f"  Expected: {clauses}")
                    else:
                        print(f"  ✓ Generated {len(section_data)} clause assessments (all clauses present)")
                    
                    all_assessments.extend(section_data)
                else:
                    print(f"  ✗ Warning: {section_name} returned non-list data: {type(section_data)}")
            except Exception as e:
                print(f"  ✗ Error generating {section_name}: {e}")
                import traceback
                traceback.print_exc()
                # Continue with other sections even if one fails
                continue
        
        if not all_assessments:
            return jsonify({"error": "Failed to generate any assessments"}), 500

        # Validate all expected clauses are present
        expected_clauses = []
        for _, clauses in sections:
            expected_clauses.extend(clauses)
        
        generated_clause_nums = []
        for assessment in all_assessments:
            clause = assessment.get('clause', '')
            clause_num = clause.split(' ')[0] if ' ' in clause else clause
            generated_clause_nums.append(clause_num)
        
        missing_clauses = [c for c in expected_clauses if c not in generated_clause_nums]
        if missing_clauses:
            print(f"\n⚠ WARNING: {len(missing_clauses)} clauses are missing from the assessment:")
            print(f"  Missing: {missing_clauses}")
            print(f"  Generated: {len(generated_clause_nums)} clauses")
            print(f"  Expected: {len(expected_clauses)} clauses")
        else:
            print(f"\n✓ All {len(expected_clauses)} expected clauses were generated successfully!")

        # 4. Save to YOUR System
        saved_path = save_locally(file.filename, all_assessments)
        print(f"Saved assessment to: {saved_path}")
        print(f"Total: Generated {len(all_assessments)} clause assessments")

        # Return only assessments - compliance report will be generated after user completes assessment
        return jsonify(all_assessments)

    except Exception as e:
        print(f"Error: {e}")
        return jsonify({"error": str(e)}), 500

# --- ROUTE: GENERATE COMPLIANCE REPORT ---
@app.route('/generate_compliance_report', methods=['POST'])
def generate_compliance_report_route():
    """Generate compliance report based on user's actual assessment selections"""
    try:
        data = request.json
        filename = data.get('filename')
        
        if not filename:
            return jsonify({"error": "Filename is required"}), 400
        
        # Load assessments with user selections
        assessment_path = find_existing_assessment(filename)
        if not assessment_path:
            return jsonify({"error": "No assessment found. Please analyze a document first."}), 404
        
        print(f"Loading assessment from: {assessment_path}")
        assessments = load_assessment(assessment_path)
        if not assessments:
            return jsonify({"error": "Failed to load assessments"}), 500
        
        # Check if user has made any selections - be more thorough
        selections_found = []
        clauses_with_level = 0
        clauses_with_score = 0
        
        for idx, assessment in enumerate(assessments):
            selected_level = assessment.get('selected_maturity_level')
            calculated_score = assessment.get('calculated_score')
            
            # Check for selected_maturity_level (None means not selected, any number 1-4 means selected)
            if selected_level is not None:
                clauses_with_level += 1
                clause_name = assessment.get('clause', f'Clause {idx}')
                selections_found.append(f"{clause_name}: Level {selected_level}")
            
            # Check for calculated_score (even if percentage is 0, the field existing means practices were assessed)
            if calculated_score:
                clauses_with_score += 1
                if isinstance(calculated_score, dict):
                    score_pct = calculated_score.get('percentage', 0)
                    total = calculated_score.get('total', 0)
                    max_score = calculated_score.get('max', 0)
                    if max_score > 0:  # Only count if practices were actually assessed
                        selections_found.append(f"Clause {idx}: Score {score_pct:.1f}% ({total:.2f}/{max_score:.2f})")
        
        # Has selections if any clause has a maturity level selected OR has practices assessed
        has_selections = clauses_with_level > 0 or clauses_with_score > 0
        
        print(f"\n{'='*60}")
        print(f"Selection Analysis:")
        print(f"  Total clauses: {len(assessments)}")
        print(f"  Clauses with maturity level: {clauses_with_level}")
        print(f"  Clauses with calculated score: {clauses_with_score}")
        if selections_found:
            print(f"  Sample selections: {selections_found[:3]}")
        print(f"{'='*60}\n")
        
        if has_selections:
            print(f"✓ Generating compliance report based on user selections...")
            print(f"  Using {clauses_with_level} clauses with selected maturity levels")
            print(f"  Using {clauses_with_score} clauses with practice scores")
        else:
            print("⚠ No selections found in assessment file.")
            print("  Generating report with default values (Level 1 for all clauses).")
            print("  If you made selections, please click 'Save Selections' first.")
        
        # Generate compliance report based on actual selections (or defaults)
        compliance_report = generate_compliance_report(assessments)
        
        if not compliance_report:
            return jsonify({"error": "Failed to generate compliance report"}), 500
        
        # Save the report
        report_path = save_compliance_report(filename, compliance_report)
        print(f"Saved compliance report to: {report_path}")
        
        return jsonify(compliance_report)
        
    except Exception as e:
        print(f"Error generating compliance report: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

# --- ROUTE: SAVE USER SELECTIONS ---
@app.route('/save_selections', methods=['POST'])
def save_selections():
    """Save user selections and update scores"""
    try:
        data = request.json
        filename = data.get('filename')
        selections = data.get('selections', {})
        
        if not filename:
            return jsonify({"error": "Filename is required"}), 400
        
        # Find existing assessment
        existing_assessment_path = find_existing_assessment(filename)
        if not existing_assessment_path:
            return jsonify({"error": "No existing assessment found"}), 404
        
        # Load existing assessment
        assessments = load_assessment(existing_assessment_path)
        if not assessments:
            return jsonify({"error": "Failed to load assessment"}), 500
        
        # Update scores based on selections
        for clause_idx, clause_selection in selections.items():
            clause_idx = int(clause_idx)
            if clause_idx < len(assessments):
                clause = assessments[clause_idx]
                
                # Update maturity level selection
                if 'maturity_level' in clause_selection:
                    # Store selected maturity level
                    if 'selected_maturity_level' not in clause:
                        clause['selected_maturity_level'] = clause_selection['maturity_level']
                    else:
                        clause['selected_maturity_level'] = clause_selection['maturity_level']
                
                # Update practice checkboxes and calculate score
                if 'practices' in clause_selection and 'maturity_level' in clause_selection:
                    maturity_level = clause_selection['maturity_level']
                    selected_practices = clause_selection['practices']
                    
                    # Find the maturity level
                    for ml in clause.get('maturity_levels', []):
                        if ml['level'] == maturity_level:
                            # Calculate total score based on selected practices
                            total_score = 0.0
                            max_score = 0.0
                            
                            for practice_idx, is_selected in selected_practices.items():
                                practice_idx = int(practice_idx)
                                if practice_idx < len(ml['practices']):
                                    practice = ml['practices'][practice_idx]
                                    max_score += practice['score']
                                    if is_selected:
                                        total_score += practice['score']
                            
                            # Store calculated score
                            clause['calculated_score'] = {
                                'total': total_score,
                                'max': max_score,
                                'percentage': (total_score / max_score * 100) if max_score > 0 else 0
                            }
                            break
        
        # Save updated assessment
        with open(existing_assessment_path, 'w', encoding='utf-8') as f:
            json.dump(assessments, f, indent=4)
        
        return jsonify({
            "success": True,
            "message": "Selections saved successfully",
            "assessments": assessments
        })
        
    except Exception as e:
        print(f"Error saving selections: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

# --- ROUTE: HEALTH CHECK ---
@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({"status": "ok", "message": "Server is running"}), 200

# --- ROUTE: UI ---
@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

# --- FRONTEND (Purple Cards) ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>GenAI ISO Auditor</title>
    <link href="[https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css](https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css)" rel="stylesheet">
    <style>
        body { background-color: #f3f4f6; font-family: 'Segoe UI', sans-serif; padding-bottom: 50px; }
        .container { max-width: 900px; margin-top: 40px; }
        .upload-card { background: white; padding: 30px; border-radius: 15px; box-shadow: 0 4px 20px rgba(0,0,0,0.05); text-align: center; margin-bottom: 30px; }
        .clause-card { background: white; border-radius: 12px; box-shadow: 0 2px 10px rgba(0,0,0,0.05); overflow: hidden; margin-bottom: 25px; border: 1px solid #e5e7eb; animation: fadeIn 0.5s; }
        @keyframes fadeIn { from { opacity:0; transform: translateY(10px); } to { opacity:1; transform: translateY(0); } }
        .clause-header { background: #6b46c1; color: white; padding: 15px 20px; font-size: 1.2rem; font-weight: bold; }
        .card-body { padding: 25px; }
        .critical-question { background: #f9fafb; padding: 15px; border-radius: 8px; margin-bottom: 20px; border-left: 4px solid #6b46c1; }
        .maturity-box { border: 1px solid #e5e7eb; border-radius: 8px; padding: 20px; margin-bottom: 15px; cursor: pointer; transition: all 0.2s; }
        .maturity-box:hover { border-color: #6b46c1; background: #faf5ff; }
        .maturity-box.selected { border-color: #6b46c1; background: #f3e8ff; box-shadow: 0 0 0 2px rgba(107, 70, 193, 0.2); }
        .level-title { font-weight: bold; color: #4b5563; }
        .practice-item { display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid #eee; font-size: 0.95rem; }
        .score-badge { font-weight: bold; color: #6b46c1; }
        .score-display { background: #e0e7ff !important; border-left: 4px solid #6b46c1; }
        #loading { display:none; }
        
        /* Compliance Scoreboard Styles */
        .scoreboard-container { background: white; border-radius: 20px; box-shadow: 0 10px 40px rgba(0,0,0,0.1); padding: 40px; margin-top: 40px; margin-bottom: 30px; }
        .scoreboard-header { border-bottom: 4px solid #6b46c1; padding-bottom: 25px; margin-bottom: 35px; }
        .scoreboard-header h2 { color: #6b46c1; font-weight: 700; margin: 0; font-size: 2rem; }
        .maturity-gauge { text-align: center; padding: 40px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); border-radius: 20px; color: white; margin-bottom: 40px; box-shadow: 0 8px 24px rgba(102, 126, 234, 0.3); }
        .maturity-gauge .score-number { font-size: 5rem; font-weight: 700; margin: 15px 0; text-shadow: 0 2px 10px rgba(0,0,0,0.2); }
        .maturity-gauge .score-label { font-size: 1.3rem; opacity: 0.95; font-weight: 500; }
        .maturity-gauge .score-percentage { font-size: 2rem; font-weight: 600; margin-top: 15px; }
        .progress-bar-container { background: rgba(255,255,255,0.25); height: 12px; border-radius: 10px; margin-top: 25px; overflow: hidden; box-shadow: inset 0 2px 4px rgba(0,0,0,0.1); }
        .progress-bar-fill { height: 100%; transition: width 1s ease-in-out; border-radius: 10px; }
        .gap-section { margin-bottom: 40px; }
        .gap-card { background: #ffffff; border-left: 5px solid #ef4444; padding: 20px; border-radius: 12px; margin-bottom: 18px; box-shadow: 0 2px 8px rgba(0,0,0,0.05); transition: transform 0.2s, box-shadow 0.2s; }
        .gap-card:hover { transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0,0,0,0.1); }
        .gap-card.moderate { border-left-color: #f59e0b; }
        .gap-card.minor { border-left-color: #10b981; }
        .priority-badge { display: inline-block; padding: 6px 14px; border-radius: 25px; font-size: 0.8rem; font-weight: 700; margin-left: 12px; text-transform: uppercase; letter-spacing: 0.5px; }
        .priority-critical { background: #fee2e2; color: #991b1b; border: 1px solid #fecaca; }
        .priority-high { background: #fed7aa; color: #92400e; border: 1px solid #fdba74; }
        .priority-medium { background: #dbeafe; color: #1e40af; border: 1px solid #93c5fd; }
        .priority-low { background: #d1fae5; color: #065f46; border: 1px solid #86efac; }
        .recommendation-card { background: #ffffff; border: 2px solid #e5e7eb; border-radius: 12px; padding: 25px; margin-bottom: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.05); transition: border-color 0.2s; }
        .recommendation-card:hover { border-color: #6b46c1; }
        .roadmap-phase { background: linear-gradient(135deg, #f9fafb 0%, #f3f4f6 100%); border-radius: 15px; padding: 25px; margin-bottom: 25px; border-left: 6px solid #6b46c1; box-shadow: 0 4px 12px rgba(0,0,0,0.05); }
        .roadmap-phase h4 { color: #6b46c1; margin-bottom: 18px; font-weight: 700; font-size: 1.3rem; }
        .priority-matrix-item { padding: 15px; background: white; border-radius: 10px; margin-bottom: 12px; border-left: 4px solid #6b46c1; box-shadow: 0 2px 6px rgba(0,0,0,0.05); transition: transform 0.2s; }
        .priority-matrix-item:hover { transform: translateX(5px); }
        .section-title { color: #1f2937; font-weight: 700; font-size: 1.4rem; margin-bottom: 20px; padding-bottom: 15px; border-bottom: 3px solid #e5e7eb; display: flex; align-items: center; gap: 10px; }
        .stat-card { background: white; border-radius: 12px; padding: 20px; text-align: center; box-shadow: 0 2px 8px rgba(0,0,0,0.05); }
        .stat-number { font-size: 2.5rem; font-weight: 700; color: #6b46c1; }
        .stat-label { font-size: 0.9rem; color: #6b7280; margin-top: 5px; }
        .gap-2 { gap: 1rem; }
        .d-flex { display: flex; }
        .flex-fill { flex: 1; }
    </style>
</head>
<body>

<div class="container">
    <div class="upload-card">
        <h2 class="mb-3">⚡ GenAI ISO Auditor</h2>
        <p class="text-muted">Powered by OpenAI • Saves to Local Disk</p>
        <input type="file" id="fileInput" class="form-control mb-3">
        <button onclick="runAnalysis()" id="btn" class="btn btn-primary w-100 py-2">Analyze Document</button>
        
        <div id="loading" class="mt-3">
            <div class="spinner-border text-primary" role="status"></div>
            <p class="mt-2">Sending to OpenAI... (Takes ~10 seconds)</p>
        </div>
    </div>

    <div id="checklist-container"></div>
    <div id="scoreboard-container" class="scoreboard-container" style="display: none;"></div>
</div>

<script>
    let currentFilename = '';
    let currentSelections = {};
    let currentComplianceReport = null;

    // Check server connectivity on page load
    window.addEventListener('DOMContentLoaded', async () => {
        try {
            const response = await fetch('/health');
            if (response.ok) {
                console.log('✓ Server is running');
            }
        } catch (e) {
            console.warn('⚠ Server health check failed:', e);
            alert('Warning: Cannot connect to server. Please make sure the Flask server is running on port 5000.');
        }
    });

    async function runAnalysis() {
        const fileInput = document.getElementById('fileInput');
        const btn = document.getElementById('btn');
        const container = document.getElementById('checklist-container');
        const scoreboardContainer = document.getElementById('scoreboard-container');
        const loading = document.getElementById('loading');
        
        if(!fileInput.files[0]) return alert("Please select a file.");
        
        currentFilename = fileInput.files[0].name;
        currentSelections = {};
        currentComplianceReport = null;
        container.innerHTML = "";
        scoreboardContainer.style.display = "none";
        btn.disabled = true;
        loading.style.display = "block";

        const fd = new FormData();
        fd.append('file', fileInput.files[0]);

        try {
            const response = await fetch('/analyze', { method: 'POST', body: fd });
            
            if (!response.ok) {
                const errorText = await response.text();
                let errorData;
                try {
                    errorData = JSON.parse(errorText);
                } catch {
                    errorData = { error: `Server error: ${response.status} ${response.statusText}` };
                }
                throw new Error(errorData.error || `Server error: ${response.status}`);
            }
            
            const data = await response.json();
            
            if (data.error) throw new Error(data.error);
            
            // Handle both old format (array) and new format (object with assessments)
            let assessments = Array.isArray(data) ? data : data.assessments || data;
            
            if (!assessments || assessments.length === 0) {
                throw new Error("No assessments were generated. Please try again.");
            }
            
            renderChecklist(assessments);

        } catch (e) {
            console.error("Error details:", e);
            let errorMessage = "An error occurred: ";
            if (e.message) {
                errorMessage += e.message;
            } else if (e.name === "TypeError" && e.message.includes("fetch")) {
                errorMessage += "Failed to connect to server. Please make sure the server is running.";
            } else {
                errorMessage += "Unknown error. Please check the console for details.";
            }
            alert(errorMessage);
        } finally {
            btn.disabled = false;
            loading.style.display = "none";
        }
    }

    function renderChecklist(data) {
        const container = document.getElementById('checklist-container');
        
        // Initialize selections for each clause
        data.forEach((clause, idx) => {
            if (!currentSelections[idx]) {
                currentSelections[idx] = {
                    maturity_level: null,
                    practices: {}
                };
            }
        });
        
        data.forEach((clause, idx) => {
            const card = document.createElement('div');
            card.className = 'clause-card';
            
            // Check if there's a saved calculated score
            const scoreDisplay = clause.calculated_score ? 
                `<div class="score-display mb-3 p-2 bg-light rounded">
                    <strong>Score: ${clause.calculated_score.total.toFixed(2)} / ${clause.calculated_score.max.toFixed(2)} 
                    (${clause.calculated_score.percentage.toFixed(1)}%)</strong>
                </div>` : '';
            
            let maturityHTML = '';
            
            if(clause.maturity_levels) {
                clause.maturity_levels.forEach(level => {
                    let practicesHTML = '';
                    if(level.practices) {
                        level.practices.forEach((p, pIdx) => {
                            const checkboxId = `p-${idx}-${level.level}-${pIdx}`;
                            practicesHTML += `
                                <div class="practice-item">
                                    <div class="form-check">
                                        <input class="form-check-input practice-checkbox" 
                                               type="checkbox" 
                                               id="${checkboxId}"
                                               data-clause="${idx}"
                                               data-level="${level.level}"
                                               data-practice="${pIdx}"
                                               onchange="updateScore(${idx})">
                                        <label class="form-check-label" for="${checkboxId}">${p.text}</label>
                                    </div>
                                    <span class="score-badge">(${p.score})</span>
                                </div>`;
                        });
                    }

                    const isSelected = clause.selected_maturity_level === level.level;
                    maturityHTML += `
                        <div class="maturity-box ${isSelected ? 'selected' : ''}" 
                             onclick="selectLevel(this, ${idx}, ${level.level})">
                            <div class="form-check pointer-events-none">
                                <input class="form-check-input" 
                                       type="radio" 
                                       name="maturity-${idx}" 
                                       id="m-${idx}-${level.level}"
                                       ${isSelected ? 'checked' : ''}>
                                <label class="form-check-label level-title" for="m-${idx}-${level.level}">
                                    Maturity Level ${level.level}
                                </label>
                            </div>
                            <p class="small text-muted mb-2 mt-1">${level.description}</p>
                            <div class="practices-list ms-4" id="practices-${idx}-${level.level}" 
                                 style="display: ${isSelected ? 'block' : 'none'};">
                                ${practicesHTML}
                            </div>
                        </div>
                    `;
                });
            }

            // DO NOT display requirements section or completeness verification to end user
            // These are for internal validation only

            card.innerHTML = `
                <div class="clause-header">Clause ${clause.clause}</div>
                <div class="card-body">
                    ${scoreDisplay}
                    <div class="critical-question">
                        <strong>Critical Question:</strong>
                        <p class="mb-2">${clause.critical_question || 'No critical question available'}</p>
                        <div>
                            <div class="form-check form-check-inline">
                                <input class="form-check-input" type="radio" name="q-${idx}" value="yes">
                                <label class="form-check-label">Yes</label>
                            </div>
                            <div class="form-check form-check-inline">
                                <input class="form-check-input" type="radio" name="q-${idx}" value="no">
                                <label class="form-check-label">No</label>
                            </div>
                        </div>
                    </div>
                    ${maturityHTML}
                </div>
            `;
            container.appendChild(card);
        });
        
        // Add save button and generate report button
        const buttonContainer = document.createElement('div');
        buttonContainer.className = 'd-flex gap-2 mt-3';
        
        const saveBtn = document.createElement('button');
        saveBtn.className = 'btn btn-success flex-fill';
        saveBtn.textContent = '💾 Save Selections';
        saveBtn.onclick = saveSelections;
        
        const generateReportBtn = document.createElement('button');
        generateReportBtn.className = 'btn btn-primary flex-fill';
        generateReportBtn.innerHTML = '📊 Generate Compliance Report';
        generateReportBtn.onclick = generateComplianceReport;
        generateReportBtn.id = 'generate-report-btn';
        
        buttonContainer.appendChild(saveBtn);
        buttonContainer.appendChild(generateReportBtn);
        container.appendChild(buttonContainer);
    }

    function selectLevel(element, clauseIdx, level) {
        const card = element.closest('.clause-card');
        card.querySelectorAll('.maturity-box').forEach(el => el.classList.remove('selected'));
        element.classList.add('selected');
        const radio = element.querySelector('input[type="radio"]');
        if(radio) radio.checked = true;
        
        // Hide all practice lists
        card.querySelectorAll('[id^="practices-"]').forEach(el => el.style.display = 'none');
        // Show practices for selected level
        const practicesDiv = document.getElementById(`practices-${clauseIdx}-${level}`);
        if(practicesDiv) practicesDiv.style.display = 'block';
        
        // Update selections
        currentSelections[clauseIdx] = currentSelections[clauseIdx] || { practices: {} };
        currentSelections[clauseIdx].maturity_level = level;
        currentSelections[clauseIdx].practices = {};
        
        // Update score
        updateScore(clauseIdx);
    }

    function updateScore(clauseIdx) {
        const clause = currentSelections[clauseIdx];
        if (!clause || !clause.maturity_level) return;
        
        // Find the clause card
        const cards = document.querySelectorAll('.clause-card');
        if (clauseIdx >= cards.length) return;
        
        const card = cards[clauseIdx];
        const practicesDiv = document.getElementById(`practices-${clauseIdx}-${clause.maturity_level}`);
        if (!practicesDiv) return;
        
        // Get all checkboxes for this level
        const checkboxes = practicesDiv.querySelectorAll('.practice-checkbox');
        let totalScore = 0;
        let maxScore = 0;
        
        checkboxes.forEach((checkbox, idx) => {
            const score = parseFloat(checkbox.nextElementSibling.nextElementSibling.textContent.match(/\(([\d.]+)\)/)[1]);
            maxScore += score;
            if (checkbox.checked) {
                totalScore += score;
                clause.practices[idx] = true;
            } else {
                clause.practices[idx] = false;
            }
        });
        
        // Update or create score display
        let scoreDisplay = card.querySelector('.score-display');
        if (!scoreDisplay) {
            scoreDisplay = document.createElement('div');
            scoreDisplay.className = 'score-display mb-3 p-2 bg-light rounded';
            card.querySelector('.card-body').insertBefore(scoreDisplay, card.querySelector('.card-body').firstChild);
        }
        const percentage = maxScore > 0 ? (totalScore / maxScore * 100) : 0;
        scoreDisplay.innerHTML = `<strong>Score: ${totalScore.toFixed(2)} / ${maxScore.toFixed(2)} (${percentage.toFixed(1)}%)</strong>`;
    }

    async function saveSelections() {
        if (!currentFilename) {
            alert('Please analyze a document first');
            return;
        }
        
        try {
            const response = await fetch('/save_selections', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    filename: currentFilename,
                    selections: currentSelections
                })
            });
            
            const data = await response.json();
            if (data.error) {
                alert('Error: ' + data.error);
            } else {
                alert('Selections saved successfully!');
            }
        } catch (e) {
            console.error("Save error:", e);
            let errorMessage = "Error saving: ";
            if (e.message) {
                errorMessage += e.message;
            } else if (e.name === "TypeError" && e.message.includes("fetch")) {
                errorMessage += "Failed to connect to server. Please make sure the server is running.";
            } else {
                errorMessage += "Unknown error. Please check the console for details.";
            }
            alert(errorMessage);
        }
    }

    async function generateComplianceReport() {
        if (!currentFilename) {
            alert('Please analyze a document first');
            return;
        }
        
        // Check if user has made selections but not saved them
        const hasUnsavedSelections = Object.keys(currentSelections).some(idx => {
            const sel = currentSelections[idx];
            return sel && (sel.maturity_level !== null || Object.keys(sel.practices || {}).length > 0);
        });
        
        if (hasUnsavedSelections) {
            const shouldSave = confirm('You have unsaved selections. Would you like to save them before generating the report? (Click OK to save, Cancel to generate with saved data)');
            if (shouldSave) {
                try {
                    await saveSelections();
                    alert('Selections saved! Now generating compliance report...');
                } catch (e) {
                    alert('Error saving selections. Generating report with previously saved data...');
                }
            }
        }
        
        const btn = document.getElementById('generate-report-btn');
        const originalText = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '⏳ Generating...';
        
        try {
            const response = await fetch('/generate_compliance_report', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ filename: currentFilename })
            });
            
            if (!response.ok) {
                const errorText = await response.text();
                let errorData;
                try {
                    errorData = JSON.parse(errorText);
                } catch {
                    errorData = { error: `Server error: ${response.status} ${response.statusText}` };
                }
                throw new Error(errorData.error || `Server error: ${response.status}`);
            }
            
            const data = await response.json();
            if (data.error) {
                alert('Error: ' + data.error);
            } else {
                currentComplianceReport = data;
                renderComplianceScoreboard(data);
                btn.innerHTML = '✅ Report Generated';
                setTimeout(() => {
                    btn.innerHTML = originalText;
                }, 2000);
            }
        } catch (e) {
            console.error("Report generation error:", e);
            let errorMessage = "Error generating report: ";
            if (e.message) {
                errorMessage += e.message;
            } else if (e.name === "TypeError" && e.message.includes("fetch")) {
                errorMessage += "Failed to connect to server. Please make sure the server is running.";
            } else {
                errorMessage += "Unknown error. Please check the console for details.";
            }
            alert(errorMessage);
            btn.innerHTML = originalText;
        } finally {
            btn.disabled = false;
        }
    }

    function renderComplianceScoreboard(report) {
        const container = document.getElementById('scoreboard-container');
        if (!report || !container) return;
        
        const maturityScore = report.overall_maturity_score || 'N/A';
        const maturityNumeric = report.overall_maturity_numeric || 0;
        const percentageScore = report.overall_percentage_score || 0;
        const gapAnalysis = report.gap_analysis || {};
        const recommendations = report.recommendations || [];
        const roadmap = report.roadmap_to_level_4 || {};
        const priorityMatrix = report.priority_matrix || {};
        
        // Calculate progress percentage (out of 4 levels)
        const progressPercent = (maturityNumeric / 4) * 100;
        const progressColor = progressPercent >= 75 ? '#10b981' : progressPercent >= 50 ? '#f59e0b' : '#ef4444';
        
        // Calculate statistics
        const criticalCount = gapAnalysis.critical_gaps?.length || 0;
        const moderateCount = gapAnalysis.moderate_gaps?.length || 0;
        const minorCount = gapAnalysis.minor_gaps?.length || 0;
        const totalGaps = criticalCount + moderateCount + minorCount;
        
        let html = `
            <div class="scoreboard-header">
                <h2>📊 Compliance Scoreboard</h2>
                <p style="color: #6b7280; margin-top: 10px; margin-bottom: 0;">Comprehensive assessment based on your selections</p>
            </div>
            
            <div class="maturity-gauge">
                <div class="score-label">Overall Maturity Level</div>
                <div class="score-number">${maturityScore}</div>
                <div class="score-percentage">${percentageScore.toFixed(1)}% Compliance</div>
                <div class="progress-bar-container">
                    <div class="progress-bar-fill" style="background: ${progressColor}; width: ${progressPercent}%;"></div>
                </div>
                <div class="score-label" style="margin-top: 20px;">${report.total_clauses || 0} Clauses Assessed</div>
            </div>
            
            <div class="d-flex gap-2" style="margin-bottom: 40px;">
                <div class="stat-card flex-fill">
                    <div class="stat-number" style="color: #ef4444;">${criticalCount}</div>
                    <div class="stat-label">Critical Gaps</div>
                </div>
                <div class="stat-card flex-fill">
                    <div class="stat-number" style="color: #f59e0b;">${moderateCount}</div>
                    <div class="stat-label">Moderate Gaps</div>
                </div>
                <div class="stat-card flex-fill">
                    <div class="stat-number" style="color: #10b981;">${minorCount}</div>
                    <div class="stat-label">Minor Gaps</div>
                </div>
                <div class="stat-card flex-fill">
                    <div class="stat-number" style="color: #6b46c1;">${report.total_clauses - totalGaps}</div>
                    <div class="stat-label">Compliant</div>
                </div>
            </div>
            
            <div class="gap-section">
                <div class="section-title">📋 Executive Summary</div>
                <p style="font-size: 1.05rem; line-height: 1.6; color: #4b5563;">${report.executive_summary || 'No summary available.'}</p>
            </div>
            
            <div class="gap-section">
                <div class="section-title">⚠️ Gap Analysis</div>
        `;
        
        // Critical Gaps
        if (gapAnalysis.critical_gaps && gapAnalysis.critical_gaps.length > 0) {
            html += `<h5 style="color: #991b1b; margin-top: 20px; margin-bottom: 15px; font-weight: 700;">Critical Gaps (${gapAnalysis.critical_gaps.length})</h5>`;
            gapAnalysis.critical_gaps.forEach(gap => {
                const selectedLevel = gap.selected_level !== undefined ? gap.selected_level : gap.current_level;
                const gapDetails = gap.gap_details || {};
                const userSelection = gapDetails.user_selection || [];
                const missingAtCurrent = gapDetails.missing_at_current_level || [];
                const roadmap = gapDetails.roadmap_to_level_4 || [];
                
                html += `
                    <div class="gap-card">
                        <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 15px;">
                            <strong style="font-size: 1.2rem; color: #1f2937;">${gap.clause} - ${gap.clause_name}</strong>
                            <span class="priority-badge priority-critical">Critical</span>
                        </div>
                        
                        <!-- Your Selection Summary -->
                        <div style="background: #e0e7ff; padding: 15px; border-radius: 8px; margin-bottom: 15px; border-left: 4px solid #6b46c1;">
                            <strong style="color: #6b46c1; font-size: 1rem; display: block; margin-bottom: 10px;">📌 Your Selection:</strong>
                            ${userSelection.map(item => `
                                <div style="margin-bottom: 8px; color: #4b5563;">
                                    <strong>${item.title}:</strong> ${item.content}
                                </div>
                            `).join('')}
                            <div style="margin-top: 8px; font-size: 0.9rem; color: #374151;">
                                <strong>Target:</strong> Level ${gap.target_level} | <strong>Gap:</strong> ${(gap.target_level - gap.current_level).toFixed(1)} levels
                            </div>
                        </div>
                        
                        <!-- What's Missing at Current Level -->
                        ${missingAtCurrent.length > 0 ? `
                            <div style="margin-bottom: 15px; padding: 15px; background: #fef3c7; border-left: 4px solid #f59e0b; border-radius: 6px;">
                                <strong style="color: #92400e; font-size: 1rem; display: block; margin-bottom: 10px;">⚠️ What's Missing at Level ${selectedLevel}:</strong>
                                <ul style="margin: 0; padding-left: 20px; color: #78350f;">
                                    ${missingAtCurrent.map(item => `<li style="margin-bottom: 6px;">${item}</li>`).join('')}
                                </ul>
                            </div>
                        ` : ''}
                        
                        <!-- Roadmap to Level 4 -->
                        ${roadmap.length > 0 ? `
                            <div style="margin-bottom: 15px; padding: 15px; background: #dbeafe; border-left: 4px solid #3b82f6; border-radius: 6px;">
                                <strong style="color: #1e40af; font-size: 1rem; display: block; margin-bottom: 12px;">🎯 Roadmap to Level 4:</strong>
                                ${roadmap.map((step, idx) => `
                                    <div style="margin-bottom: ${idx < roadmap.length - 1 ? '15px' : '0'}; padding: 12px; background: white; border-radius: 6px; border-left: 3px solid #3b82f6;">
                                        <div style="display: flex; align-items: center; margin-bottom: 8px;">
                                            <span style="background: #3b82f6; color: white; width: 24px; height: 24px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center; font-weight: bold; margin-right: 10px; font-size: 0.85rem;">${step.step}</span>
                                            <strong style="color: #1e40af; font-size: 0.95rem;">${step.title}</strong>
                                        </div>
                                        <p style="margin: 8px 0 8px 34px; color: #4b5563; font-size: 0.9rem; line-height: 1.5;">${step.description}</p>
                                        ${step.practices && step.practices.length > 0 ? `
                                            <div style="margin-left: 34px; margin-top: 8px;">
                                                <strong style="color: #1e40af; font-size: 0.85rem; display: block; margin-bottom: 6px;">Key Practices to Implement:</strong>
                                                <ul style="margin: 0; padding-left: 20px; color: #374151; font-size: 0.85rem;">
                                                    ${step.practices.map(practice => `<li style="margin-bottom: 4px;">${practice}</li>`).join('')}
                                                </ul>
                                            </div>
                                        ` : ''}
                                    </div>
                                `).join('')}
                            </div>
                        ` : ''}
                    </div>
                `;
            });
        }
        
        // Moderate Gaps
        if (gapAnalysis.moderate_gaps && gapAnalysis.moderate_gaps.length > 0) {
            html += `<h5 style="color: #92400e; margin-top: 20px; margin-bottom: 15px; font-weight: 700;">Moderate Gaps (${gapAnalysis.moderate_gaps.length})</h5>`;
            gapAnalysis.moderate_gaps.forEach(gap => {
                const selectedLevel = gap.selected_level !== undefined ? gap.selected_level : gap.current_level;
                const gapDetails = gap.gap_details || {};
                const userSelection = gapDetails.user_selection || [];
                const missingAtCurrent = gapDetails.missing_at_current_level || [];
                const roadmap = gapDetails.roadmap_to_level_4 || [];
                
                html += `
                    <div class="gap-card moderate">
                        <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 15px;">
                            <strong style="font-size: 1.2rem; color: #1f2937;">${gap.clause} - ${gap.clause_name}</strong>
                            <span class="priority-badge priority-high">High</span>
                        </div>
                        
                        <!-- Your Selection Summary -->
                        <div style="background: #e0e7ff; padding: 15px; border-radius: 8px; margin-bottom: 15px; border-left: 4px solid #6b46c1;">
                            <strong style="color: #6b46c1; font-size: 1rem; display: block; margin-bottom: 10px;">📌 Your Selection:</strong>
                            ${userSelection.map(item => `
                                <div style="margin-bottom: 8px; color: #4b5563;">
                                    <strong>${item.title}:</strong> ${item.content}
                                </div>
                            `).join('')}
                            <div style="margin-top: 8px; font-size: 0.9rem; color: #374151;">
                                <strong>Target:</strong> Level ${gap.target_level} | <strong>Gap:</strong> ${(gap.target_level - gap.current_level).toFixed(1)} levels
                            </div>
                        </div>
                        
                        <!-- What's Missing at Current Level -->
                        ${missingAtCurrent.length > 0 ? `
                            <div style="margin-bottom: 15px; padding: 15px; background: #fef3c7; border-left: 4px solid #f59e0b; border-radius: 6px;">
                                <strong style="color: #92400e; font-size: 1rem; display: block; margin-bottom: 10px;">⚠️ What's Missing at Level ${selectedLevel}:</strong>
                                <ul style="margin: 0; padding-left: 20px; color: #78350f;">
                                    ${missingAtCurrent.map(item => `<li style="margin-bottom: 6px;">${item}</li>`).join('')}
                                </ul>
                            </div>
                        ` : ''}
                        
                        <!-- Roadmap to Level 4 -->
                        ${roadmap.length > 0 ? `
                            <div style="margin-bottom: 15px; padding: 15px; background: #dbeafe; border-left: 4px solid #3b82f6; border-radius: 6px;">
                                <strong style="color: #1e40af; font-size: 1rem; display: block; margin-bottom: 12px;">🎯 Roadmap to Level 4:</strong>
                                ${roadmap.map((step, idx) => `
                                    <div style="margin-bottom: ${idx < roadmap.length - 1 ? '15px' : '0'}; padding: 12px; background: white; border-radius: 6px; border-left: 3px solid #3b82f6;">
                                        <div style="display: flex; align-items: center; margin-bottom: 8px;">
                                            <span style="background: #3b82f6; color: white; width: 24px; height: 24px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center; font-weight: bold; margin-right: 10px; font-size: 0.85rem;">${step.step}</span>
                                            <strong style="color: #1e40af; font-size: 0.95rem;">${step.title}</strong>
                                        </div>
                                        <p style="margin: 8px 0 8px 34px; color: #4b5563; font-size: 0.9rem; line-height: 1.5;">${step.description}</p>
                                        ${step.practices && step.practices.length > 0 ? `
                                            <div style="margin-left: 34px; margin-top: 8px;">
                                                <strong style="color: #1e40af; font-size: 0.85rem; display: block; margin-bottom: 6px;">Key Practices to Implement:</strong>
                                                <ul style="margin: 0; padding-left: 20px; color: #374151; font-size: 0.85rem;">
                                                    ${step.practices.map(practice => `<li style="margin-bottom: 4px;">${practice}</li>`).join('')}
                                                </ul>
                                            </div>
                                        ` : ''}
                                    </div>
                                `).join('')}
                            </div>
                        ` : ''}
                    </div>
                `;
            });
        }
        
        // Minor Gaps
        if (gapAnalysis.minor_gaps && gapAnalysis.minor_gaps.length > 0) {
            html += `<h5 style="color: #065f46; margin-top: 20px; margin-bottom: 15px; font-weight: 700;">Minor Gaps (${gapAnalysis.minor_gaps.length})</h5>`;
            gapAnalysis.minor_gaps.forEach(gap => {
                const selectedLevel = gap.selected_level !== undefined ? gap.selected_level : gap.current_level;
                const gapDetails = gap.gap_details || {};
                const userSelection = gapDetails.user_selection || [];
                const missingAtCurrent = gapDetails.missing_at_current_level || [];
                const roadmap = gapDetails.roadmap_to_level_4 || [];
                
                html += `
                    <div class="gap-card minor">
                        <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 15px;">
                            <strong style="font-size: 1.2rem; color: #1f2937;">${gap.clause} - ${gap.clause_name}</strong>
                            <span class="priority-badge priority-medium">Medium</span>
                        </div>
                        
                        <!-- Your Selection Summary -->
                        <div style="background: #e0e7ff; padding: 15px; border-radius: 8px; margin-bottom: 15px; border-left: 4px solid #6b46c1;">
                            <strong style="color: #6b46c1; font-size: 1rem; display: block; margin-bottom: 10px;">📌 Your Selection:</strong>
                            ${userSelection.map(item => `
                                <div style="margin-bottom: 8px; color: #4b5563;">
                                    <strong>${item.title}:</strong> ${item.content}
                                </div>
                            `).join('')}
                            <div style="margin-top: 8px; font-size: 0.9rem; color: #374151;">
                                <strong>Target:</strong> Level ${gap.target_level} | <strong>Gap:</strong> ${(gap.target_level - gap.current_level).toFixed(1)} levels
                            </div>
                        </div>
                        
                        <!-- What's Missing at Current Level -->
                        ${missingAtCurrent.length > 0 ? `
                            <div style="margin-bottom: 15px; padding: 15px; background: #fef3c7; border-left: 4px solid #f59e0b; border-radius: 6px;">
                                <strong style="color: #92400e; font-size: 1rem; display: block; margin-bottom: 10px;">⚠️ What's Missing at Level ${selectedLevel}:</strong>
                                <ul style="margin: 0; padding-left: 20px; color: #78350f;">
                                    ${missingAtCurrent.map(item => `<li style="margin-bottom: 6px;">${item}</li>`).join('')}
                                </ul>
                            </div>
                        ` : ''}
                        
                        <!-- Roadmap to Level 4 -->
                        ${roadmap.length > 0 ? `
                            <div style="margin-bottom: 15px; padding: 15px; background: #dbeafe; border-left: 4px solid #3b82f6; border-radius: 6px;">
                                <strong style="color: #1e40af; font-size: 1rem; display: block; margin-bottom: 12px;">🎯 Roadmap to Level 4:</strong>
                                ${roadmap.map((step, idx) => `
                                    <div style="margin-bottom: ${idx < roadmap.length - 1 ? '15px' : '0'}; padding: 12px; background: white; border-radius: 6px; border-left: 3px solid #3b82f6;">
                                        <div style="display: flex; align-items: center; margin-bottom: 8px;">
                                            <span style="background: #3b82f6; color: white; width: 24px; height: 24px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center; font-weight: bold; margin-right: 10px; font-size: 0.85rem;">${step.step}</span>
                                            <strong style="color: #1e40af; font-size: 0.95rem;">${step.title}</strong>
                                        </div>
                                        <p style="margin: 8px 0 8px 34px; color: #4b5563; font-size: 0.9rem; line-height: 1.5;">${step.description}</p>
                                        ${step.practices && step.practices.length > 0 ? `
                                            <div style="margin-left: 34px; margin-top: 8px;">
                                                <strong style="color: #1e40af; font-size: 0.85rem; display: block; margin-bottom: 6px;">Key Practices to Implement:</strong>
                                                <ul style="margin: 0; padding-left: 20px; color: #374151; font-size: 0.85rem;">
                                                    ${step.practices.map(practice => `<li style="margin-bottom: 4px;">${practice}</li>`).join('')}
                                                </ul>
                                            </div>
                                        ` : ''}
                                    </div>
                                `).join('')}
                            </div>
                        ` : ''}
                    </div>
                `;
            });
        }
        
        html += `</div>`;
        
        // Recommendations
        if (recommendations.length > 0) {
            html += `
                <div class="gap-section">
                    <div class="section-title">💡 Recommendations</div>
            `;
            recommendations.slice(0, 10).forEach(rec => {
                html += `
                    <div class="recommendation-card">
                        <h5 style="color: #6b46c1; margin-bottom: 10px;">${rec.clause} - ${rec.clause_name}</h5>
                        <ul style="margin-bottom: 10px;">
                            ${rec.action_items.map(item => `<li style="margin-bottom: 5px;">${item}</li>`).join('')}
                        </ul>
                        <div style="font-size: 0.9rem; color: #6b7280;">
                            <strong>Timeline:</strong> ${rec.timeline} | 
                            <strong>Resources:</strong> ${rec.resources_required}
                        </div>
                    </div>
                `;
            });
            html += `</div>`;
        }
        
        // Roadmap
        if (roadmap.phase_1 || roadmap.phase_2 || roadmap.phase_3) {
            html += `
                <div class="gap-section">
                    <div class="section-title">🗺️ Roadmap to Level 4</div>
            `;
            
            if (roadmap.phase_1) {
                html += `
                    <div class="roadmap-phase">
                        <h4>Phase 1: ${roadmap.phase_1.title} <span style="font-size: 0.9rem; color: #6b7280;">(${roadmap.phase_1.duration})</span></h4>
                        <p><strong>Clauses:</strong> ${roadmap.phase_1.clauses.join(', ')}</p>
                        <ul>
                            ${roadmap.phase_1.key_actions.map(action => `<li>${action}</li>`).join('')}
                        </ul>
                    </div>
                `;
            }
            
            if (roadmap.phase_2) {
                html += `
                    <div class="roadmap-phase">
                        <h4>Phase 2: ${roadmap.phase_2.title} <span style="font-size: 0.9rem; color: #6b7280;">(${roadmap.phase_2.duration})</span></h4>
                        <p><strong>Clauses:</strong> ${roadmap.phase_2.clauses.join(', ')}</p>
                        <ul>
                            ${roadmap.phase_2.key_actions.map(action => `<li>${action}</li>`).join('')}
                        </ul>
                    </div>
                `;
            }
            
            if (roadmap.phase_3) {
                html += `
                    <div class="roadmap-phase">
                        <h4>Phase 3: ${roadmap.phase_3.title} <span style="font-size: 0.9rem; color: #6b7280;">(${roadmap.phase_3.duration})</span></h4>
                        <p><strong>Clauses:</strong> ${roadmap.phase_3.clauses.join(', ')}</p>
                        <ul>
                            ${roadmap.phase_3.key_actions.map(action => `<li>${action}</li>`).join('')}
                        </ul>
                    </div>
                `;
            }
            
            html += `</div>`;
        }
        
        // Priority Matrix
        if (priorityMatrix.quick_wins || priorityMatrix.strategic_initiatives || priorityMatrix.foundational_requirements) {
            html += `
                <div class="gap-section">
                    <div class="section-title">🎯 Priority Matrix</div>
            `;
            
            if (priorityMatrix.quick_wins && priorityMatrix.quick_wins.length > 0) {
                html += `<h5 style="color: #10b981; margin-top: 15px; margin-bottom: 10px;">Quick Wins</h5>`;
                priorityMatrix.quick_wins.forEach(item => {
                    html += `<div class="priority-matrix-item">${item}</div>`;
                });
            }
            
            if (priorityMatrix.strategic_initiatives && priorityMatrix.strategic_initiatives.length > 0) {
                html += `<h5 style="color: #6b46c1; margin-top: 20px; margin-bottom: 10px;">Strategic Initiatives</h5>`;
                priorityMatrix.strategic_initiatives.forEach(item => {
                    html += `<div class="priority-matrix-item">${item}</div>`;
                });
            }
            
            if (priorityMatrix.foundational_requirements && priorityMatrix.foundational_requirements.length > 0) {
                html += `<h5 style="color: #f59e0b; margin-top: 20px; margin-bottom: 10px;">Foundational Requirements</h5>`;
                priorityMatrix.foundational_requirements.forEach(item => {
                    html += `<div class="priority-matrix-item">${item}</div>`;
                });
            }
            
            html += `</div>`;
        }
        
        container.innerHTML = html;
        container.style.display = "block";
        
        // Scroll to scoreboard
        container.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
</script>
</body>
</html>
"""

if __name__ == "__main__":
    print("Server running on [http://0.0.0.0:5000](http://0.0.0.0:5000)")
    app.run(host='0.0.0.0', port=5000, debug=True)