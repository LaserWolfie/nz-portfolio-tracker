import random

class StrategyEngine:
    def __init__(self):
        # Jan 2026 Context (Hardcoded for "Source of Truth" simulation)
        self.market_context = {
            "OCR": 4.25,
            "CPI": 3.1,
            "Business_Confidence": 15.0,  # Positive
            "Dairy_Payout": 8.50
        }
    
    def determine_regime(self):
        """
        Simulates a macro-economic regime detection.
        Real-world: Would use OCR trend + GDP Gap.
        """
        # Logic: Falling Inflation + Positive Business Confidence = Expansion
        if self.market_context['CPI'] < 4.0 and self.market_context['Business_Confidence'] > 0:
            return "Expansion", "Risk-On / Early Expansion", "green"
        elif self.market_context['OCR'] > 5.0:
            return "Contraction", "Restrictive", "red"
        else:
            return "Neutral", "Balanced", "orange"

    def get_strategy_targets(self, strategy_name):
        """
        Returns asset allocation targets based on the selected strategy
        and the current market regime.
        """
        regime, signal, color = self.determine_regime()
        
        # Base Allocation (defaults)
        targets = {"Equity": 50, "Bonds": 30, "Cash": 20}
        
        if "Cycle Purist" in strategy_name:
            # Cycle Purist: Aggressive in Expansion
            if regime == "Expansion":
                targets = {"Equity": 75, "Bonds": 20, "Cash": 5}
                sentiment = "Euphoric"
                score = 4.0
            else:
                targets = {"Equity": 40, "Bonds": 40, "Cash": 20}
                sentiment = "Cautious"
                score = 2.5
                
        elif "Momentum Chaser" in strategy_name:
            targets = {"Equity": 95, "Bonds": 0, "Cash": 5}
            sentiment = "Aggressive"
            score = 5.0
            
        elif "Wealth Shield" in strategy_name:
            targets = {"Equity": 30, "Bonds": 50, "Cash": 20}
            sentiment = "Defensive"
            score = 1.0
            
        else:
            sentiment = "Neutral"
            score = 3.0

        return {
            "targets": targets,
            "regime": regime,
            "signal": signal,
            "color": color,
            "sentiment": sentiment,
            "composite_score": score
        }