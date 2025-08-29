"""
Tests for Response Validation and Cleanup System

These tests verify that the response validation system correctly identifies
and removes unwanted content while preserving legitimate responses.
"""

import pytest
from src.core.response_validator import ResponseValidator, ResponseType


class TestResponseValidator:
    """Test cases for the ResponseValidator class"""



    def test_clean_response_with_repetitive_content(self):
        """Test removal of repetitive content like the original Llama-3.2-3B issue"""
        repetitive_response = """
        4. Any optimization recommendations?
        No optimization recommendations can be made based on the available metrics.
        
        Note: The answer to question 4 is based on the assumption that stable state indicates proper configuration.
        However, since the stable state is explicitly indicated by the "stable" labels, no optimization is needed.
        Therefore, no optimization recommendations can be made based on the available metrics.
        Note: The answer to question 4 is based on the assumption that stable state indicates proper configuration.
        However, since the stable state is explicitly indicated by the "stable" labels, no optimization is needed.
        Therefore, no optimization recommendations can be made based on the available metrics.
        """
        
        openshift_prompt = "Questions: 1. Current state? 2. Concerns? 3. Actions? 4. Optimization?"
        
        result = ResponseValidator.clean_response(repetitive_response, ResponseType.OPENSHIFT_ANALYSIS, openshift_prompt)
        
        # Should be truncated
        assert result['validation_info']['truncated'] == True
        # Should remove the repetitive "Note:" sections
        assert "Note:" in result['removed_content']
        # Cleaned response should be much shorter
        assert len(result['cleaned_response']) < len(repetitive_response) / 2

    def test_clean_response_preserves_good_content(self):
        """Test that good content is preserved without truncation"""
        good_response = """
        1. What's the current gpu & accelerators state?
        GPU temperature is 45°C and utilization is 75%. All systems operating normally.
        
        2. Are there performance or reliability concerns?
        Power usage trending upward but still within acceptable limits.
        
        3. What actions should be taken?
        Monitor power consumption trends over next 24 hours.
        
        4. Any optimization recommendations?
        Consider implementing power management policies during low usage periods.
        """
        
        openshift_prompt = "Questions: 1. Current state? 2. Concerns? 3. Actions? 4. Optimization?"
        
        result = ResponseValidator.clean_response(good_response, ResponseType.OPENSHIFT_ANALYSIS, openshift_prompt)
        
        # Should not be truncated
        assert result['validation_info']['truncated'] == False
        # No content should be removed
        assert result['removed_content'] == ""
        # Response should be preserved (maybe with whitespace normalization)
        assert len(result['cleaned_response']) >= len(good_response.strip()) * 0.9

    def test_validate_openshift_content_complete(self):
        """Test validation of complete OpenShift responses"""
        complete_response = """
        1. What's the current state?
        All systems operational.
        
        2. Are there performance concerns?
        No immediate concerns detected.
        
        3. What actions should be taken?
        Continue monitoring.
        
        4. Any optimization recommendations?
        Implement automated scaling.
        """
        
        validation = ResponseValidator.validate_required_content(
            complete_response, ResponseType.OPENSHIFT_ANALYSIS
        )
        
        assert validation['status'] == 'complete'
        assert validation['completeness_score'] == 1.0
        assert len(validation['missing_questions']) == 0
        assert len(validation['questions_found']) == 4

    def test_validate_openshift_content_incomplete(self):
        """Test validation of incomplete OpenShift responses"""
        incomplete_response = """
        1. What's the current state?
        All systems operational.
        
        2. Are there performance concerns?
        No immediate concerns detected.
        """
        
        validation = ResponseValidator.validate_required_content(
            incomplete_response, ResponseType.OPENSHIFT_ANALYSIS
        )
        
        assert validation['status'] == 'incomplete'
        assert validation['completeness_score'] == 0.5  # 2 out of 4 questions
        assert len(validation['missing_questions']) == 2
        assert len(validation['questions_found']) == 2

    def test_remove_repetitive_patterns(self):
        """Test removal of specific repetitive patterns"""
        repetitive_text = """
        System is stable. No action required.
        Note: This is based on current metrics.
        However, since metrics show stability, no action is needed.
        Note: This is based on current metrics.
        However, since metrics show stability, no action is needed.
        System is stable. No action required.
        """
        
        cleaned = ResponseValidator.remove_repetitive_patterns(repetitive_text)
        
        # Should remove duplicate sentences
        assert cleaned.count("Note: This is based on current metrics") <= 1
        assert cleaned.count("However, since metrics show stability") <= 1
        assert cleaned.count("System is stable. No action required") <= 1

    def test_normalize_whitespace(self):
        """Test whitespace normalization"""
        messy_text = """
        Line 1


        Line 2   
        
        
        
        Line 3
        """
        
        normalized = ResponseValidator._normalize_whitespace(messy_text)
        
        # Should remove excessive blank lines
        assert "\n\n\n" not in normalized
        # Should remove trailing whitespace
        assert not any(line.endswith(' ') for line in normalized.split('\n'))

    def test_remove_incomplete_sentences(self):
        """Test removal of incomplete sentences from truncation"""
        incomplete_text = "Complete sentence. Another complete sentence. Incomplete sent"
        
        cleaned = ResponseValidator._remove_incomplete_sentences(incomplete_text)
        
        # Should remove the incomplete sentence
        assert cleaned == "Complete sentence. Another complete sentence."

    def test_find_completion_point_with_indicators(self):
        """Test finding completion points based on indicators"""
        response_with_indicators = """
        Good content here.
        More good content.
        
        The final answer is:
        This should be removed.
        """
        
        completion_point = ResponseValidator.find_completion_point(
            response_with_indicators, ResponseType.GENERAL_CHAT
        )
        
        # Should find the completion indicator
        assert completion_point > 0
        assert completion_point < len(response_with_indicators)
        assert "The final answer is:" in response_with_indicators[completion_point:]


class TestIntegration:
    """Integration tests with the LLM client"""

    def test_summarize_with_llm_validation_enabled(self):
        """Test that validation is applied when enabled"""
        # This would require mocking the LLM response
        # For now, just test that the function exists and has the right signature
        from src.core.llm_client import summarize_with_llm
        
        # Check that function accepts enable_validation parameter
        import inspect
        sig = inspect.signature(summarize_with_llm)
        assert 'enable_validation' in sig.parameters
        assert sig.parameters['enable_validation'].default == True


if __name__ == "__main__":
    # Run basic tests
    validator = TestResponseValidator()
    
    print("🧪 Running Response Validation Tests...")
    

    
    # Test content cleaning
    validator.test_clean_response_with_repetitive_content()
    validator.test_clean_response_preserves_good_content()
    print("✅ Content cleaning tests passed")
    
    # Test content validation
    validator.test_validate_openshift_content_complete()
    validator.test_validate_openshift_content_incomplete()
    print("✅ Content validation tests passed")
    
    # Test utility functions
    validator.test_remove_repetitive_patterns()
    validator.test_normalize_whitespace()
    validator.test_remove_incomplete_sentences()
    validator.test_find_completion_point_with_indicators()
    print("✅ Utility function tests passed")
    
    # Test integration
    integration = TestIntegration()
    integration.test_summarize_with_llm_validation_enabled()

    print("✅ Integration tests passed")
    
    print("\n🎉 All tests passed! Response validation system is working correctly.")